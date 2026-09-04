from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
import re

from . import abundances
from .models import UNIT_KEYS, User, UserPreferences


# The model field is an unbounded TextField holding free text imported from
# clients.register - multi-line, and up to ~270 characters in the existing data.
# Both forms that write it share this cap so the edit form cannot reject a value
# the importer already stored.
AFFILIATION_MAX_LENGTH = 500

AFFILIATION_HELP = 'Institute, department and your current position'

# Shared by the three extraction forms, which had three copies of it to drift
# apart. Long is the default because it is the only format that can be converted
# to CSV, VOTable, FITS, Parquet or SQLite afterwards - it carries the term
# designations, both level energies and all three Lande factors that the short
# format drops - and the label says so, since nothing else on the page would
# tell someone why the choice matters.
EXTRACTION_FORMAT_CHOICES = [
    ('short', 'Short format'),
    ('long', 'Long format (with conversion options)'),
]
EXTRACTION_FORMAT_DEFAULT = 'long'


class UserPreferencesForm(forms.ModelForm):
    """Validates unit preferences against the model's choices.

    save_units previously wrote request.POST values straight onto the model;
    Django does not enforce choices on save() and SQLite ignores max_length, so
    arbitrary strings could persist and then feed pres_in flag generation.
    """
    class Meta:
        model = UserPreferences
        fields = ['energyunit', 'medium', 'waveunit', 'vdwformat', 'isotopic_scaling']
        # Radios rather than the ModelForm default of a <select> each: these are
        # five short, fixed choices that the per-request panel already shows as
        # radios, and /unitselection/ renders this form to get the model's own
        # option labels instead of spelling them out again. Widgets do not affect
        # validation, so save_units is unchanged by this.
        widgets = {name: forms.RadioSelect() for name in
                   ('energyunit', 'medium', 'waveunit', 'vdwformat', 'isotopic_scaling')}


# These two values are written verbatim into the control files the Fortran
# binaries read (pres_in/show_in line 3, select.input before the 'END'
# sentinel), where a stray newline shifts every following line - which would
# let a request choose its own config path or select's output filename.
# Validating the shape here also turns a typo into a form error instead of an
# opaque failure inside preselect5/select5.

# <element> [spectral number], allowing an isotope prefix (48Ca 2) and
# molecules (TiO, H2O). Deliberately permissive about the species itself.
ELEMENT_ION_RE = re.compile(r'^\d{0,3}[A-Za-z][A-Za-z0-9]{0,5}(?: \d{1,2})?$')


class ChoiceDisablingSelect(forms.Select):
    """Select that disables the choices in `disabled_values` and describes them.

    `option_descriptions` maps a choice value to prose about it, rendered both as
    the option's title (a hover tooltip - desktop only, and not announced by
    screen readers) and as a data attribute, which is where the line shown under
    the menu reads it from. Carrying it on the option rather than in a separate
    JS map keeps one copy of the text and leaves the escaping to the template.

    Set both attributes on the bound field's widget, not on the class: Django
    deep-copies base_fields per form instance, so a per-instance assignment
    cannot leak into another request.
    """
    disabled_values = ()
    option_descriptions = {}

    def create_option(self, name, value, *args, **kwargs):
        option = super().create_option(name, value, *args, **kwargs)
        if str(value) in self.disabled_values:
            option['attrs']['disabled'] = True
        description = self.option_descriptions.get(str(value))
        if description:
            option['attrs']['title'] = description
            option['attrs']['data-description'] = description
        return option


def _pref_default(name):
    """The site default for a unit, taken from the model so it cannot drift."""
    return UserPreferences._meta.get_field(name).get_default()


class UnitFieldsMixin(forms.Form):
    """Unit selection as part of the request instead of only the user profile.

    The backend already worked this way: create_job_config reads all five values
    out of Request.parameters, never off the user, so nothing downstream changes.
    UserPreferences stops being the truth and becomes the seed - see
    extract_form_view for the precedence.

    A Form subclass rather than a plain mixin because DeclarativeFieldsMetaclass
    only collects fields from bases that have `declared_fields`; declared on a
    bare object mixin they are silently dropped.

    None of the five is required. A browser always submits them, but a POST that
    predates these fields must keep meaning what it meant, which was "use my
    saved defaults" - so an omitted unit falls back to the form's initial rather
    than becoming a validation error.
    """
    energyunit = forms.ChoiceField(
        label='Energy level units', choices=UserPreferences.ENERGY_CHOICES,
        required=False, initial=_pref_default('energyunit'), widget=forms.RadioSelect)
    medium = forms.ChoiceField(
        label='Give wavelengths in medium', choices=UserPreferences.MEDIUM_CHOICES,
        required=False, initial=_pref_default('medium'), widget=forms.RadioSelect)
    waveunit = forms.ChoiceField(
        label='Wavelength units', choices=UserPreferences.WAVEUNIT_CHOICES,
        required=False, initial=_pref_default('waveunit'), widget=forms.RadioSelect)
    vdwformat = forms.ChoiceField(
        label='Van der Waals syntax', choices=UserPreferences.VDWFORMAT_CHOICES,
        required=False, initial=_pref_default('vdwformat'), widget=forms.RadioSelect)
    isotopic_scaling = forms.ChoiceField(
        label='Isotopic scaling', choices=UserPreferences.ISOTOPIC_CHOICES,
        required=False, initial=_pref_default('isotopic_scaling'),
        widget=forms.RadioSelect)
    # Rides along with the submission rather than being its own button: the page
    # is one form posting to /submit/, so a second target would navigate away and
    # lose the half-filled form this panel exists to stop losing.
    save_as_default = forms.BooleanField(
        label='Save as default units', required=False)

    def clean(self):
        """Fill in omitted units, then normalise the medium under cm^-1.

        The medium is the one a real browser can leave out: it is disabled under
        cm^-1, and a disabled radio submits nothing. Normalising rather than
        rejecting because preselect5 hard-codes cm^-1 output to vacuum wavenumbers
        and ignores the medium flag entirely - see
        test_medium_flag_is_inert_under_wavenumber_output. An air choice there is
        not wrong, just inert; storing it would leave rows this form cannot render
        honestly.
        """
        cleaned = super().clean()
        for key in UNIT_KEYS:
            if not cleaned.get(key):
                cleaned[key] = self.get_initial_for_field(self.fields[key], key)
        if cleaned.get('waveunit') == '1/cm':
            cleaned['medium'] = 'vacuum'
        return cleaned


class LinelistConfigChoiceMixin:
    """Fills in the 'Linelist configuration' menu for the request forms.

    The choices are the system configs - default.cfg and the VALD3_* variants
    imported beside it - plus the user's own snapshot. They come from the
    database rather than the field declaration, because which variants exist is
    an import-time decision, not a code one.

    'Custom' is grayed out for users without a personal config. Choosing it used
    to do nothing observable: the job ran with the system default while the
    stored request said it had used a custom one. Most accounts are in that
    state, so most users were offered a choice that could not take effect.

    Without a user that choice is treated as unavailable, so a call site that
    forgets to pass one loses the option rather than the check. No HTTP path
    gets here without one anyway: require_login guards the form views and
    submit_request refuses an anonymous extraction.

    clean_pconf is what actually enforces this - a disabled option is still
    submittable by anything that is not a browser, so the widget alone stops
    nobody.
    """

    PCONF_UNAVAILABLE_LABEL = 'Custom (no personal configuration saved)'
    PCONF_PERSONAL_LABEL = 'Custom (your own configuration)'

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .persconfig import (
            PCONF_DEFAULT, PCONF_PERSONAL, get_alternative_configs,
            get_default_config, get_user_config,
        )

        # Not `_has_personal_config(user)` with a None user: filter(user=None) is
        # filter(user__isnull=True), which matches the *system* config and would
        # report a personal one for nobody in particular.
        self.personal_config_available = user is not None and _has_personal_config(user)

        default_config = get_default_config()
        choices = [(PCONF_DEFAULT, default_config.name if default_config else 'Default')]
        choices += [(c.slug, c.name) for c in get_alternative_configs()]
        choices.append((
            PCONF_PERSONAL,
            self.PCONF_PERSONAL_LABEL if self.personal_config_available
            else self.PCONF_UNAVAILABLE_LABEL,
        ))

        # Config.description, which nothing used to show. Keyed by the same value
        # the choice carries, so 'default' describes whichever config is default
        # now rather than naming one.
        descriptions = {PCONF_DEFAULT: default_config.description if default_config else ''}
        descriptions.update({c.slug: c.description for c in get_alternative_configs()})
        if self.personal_config_available:
            personal = get_user_config(user)
            descriptions[PCONF_PERSONAL] = personal.description if personal else ''

        field = self.fields['pconf']
        field.choices = choices
        field.widget.option_descriptions = {
            value: text for value, text in descriptions.items() if text
        }
        if not self.personal_config_available:
            field.widget.disabled_values = (PCONF_PERSONAL,)

        # ?modify= of an older request prefills whatever it chose, which may be
        # a config since deleted, or 'personal' for a user who has since removed
        # theirs - a selected-but-disabled option the user cannot change. Fall
        # back to the default rather than offering a dead choice.
        selectable = {value for value, _ in choices}
        if not self.personal_config_available:
            selectable.discard(PCONF_PERSONAL)
        if self.initial.get('pconf') not in selectable:
            self.initial['pconf'] = PCONF_DEFAULT

    def clean_pconf(self):
        value = self.cleaned_data['pconf']
        if value == 'personal' and not self.personal_config_available:
            raise ValidationError(
                'You have no personal linelist configuration saved. Create one '
                'on the Linelist configuration page, or choose Default.'
            )
        return value


def _has_personal_config(user):
    """Whether this user's requests would really use a config of their own."""
    from .persconfig import get_user_config
    return get_user_config(user) is not None


def clean_element_ionization(value, field_label='Element'):
    """Normalise and validate an "element [ionization]" value."""
    collapsed = ' '.join(value.split())
    if not collapsed:
        return collapsed

    parts = collapsed.split(' ')
    if len(parts) > 1 and not parts[1].isdigit():
        raise ValidationError("Please express the ionization stage as an arabic number")

    if not ELEMENT_ION_RE.match(collapsed):
        raise ValidationError(
            f'{field_label} must be an element, optionally followed by an '
            'ionization stage - for example "Fe" or "Fe 3".'
        )

    return collapsed


def clean_chemical_composition(value):
    """Validate 'element: log abundance' pairs, preserving the user's layout.

    Grammar lives in vald.abundances, shared with the code that renders
    select.input, so validation and generation cannot drift apart.
    """
    lines = [line.strip() for line in value.splitlines()]
    lines = [line for line in lines if line]

    try:
        pairs = abundances.parse('\n'.join(lines))
    except ValueError as e:
        raise ValidationError(
            f'Could not read "{e.args[0]}" as a chemical composition entry. '
            'Give each element as <element>: <log abundance> - for example '
            '"Fe: -4.50" - separating several entries with commas. '
            '"M/H: -0.5" sets an overall metallicity.'
        )

    if len(pairs) > abundances.MAX_PAIRS:
        raise ValidationError(
            f'At most {abundances.MAX_PAIRS} abundance entries can be given '
            f'({len(pairs)} found).'
        )

    return '\n'.join(lines)


class PasswordResetRequestForm(forms.Form):
    """Password reset request form"""
    email = forms.EmailField(
        label='Email address',
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'})
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if '@' not in email:
            raise ValidationError("Your email address should at least contain a '@'!")
        return email


class PasswordResetForm(forms.Form):
    """Password reset form"""
    password = forms.CharField(
        label='New password',
        required=True,
        widget=forms.PasswordInput(attrs={'size': '40'})
    )
    password_confirm = forms.CharField(
        label='Confirm new password',
        required=True,
        widget=forms.PasswordInput(attrs={'size': '40'})
    )

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm and password != password_confirm:
            raise ValidationError("Passwords do not match.")

        # Apply AUTH_PASSWORD_VALIDATORS rather than an ad-hoc length check.
        # They were configured in settings but never invoked anywhere, and the
        # minimum here (6) disagreed with the activation form's (8).
        if password:
            validate_password(password)

        return cleaned_data


class RegistrationForm(forms.Form):
    """User registration form - creates pending user accounts for admin approval"""
    email = forms.EmailField(
        label='Email address',
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'})
    )
    name = forms.CharField(
        label='Full name',
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'})
    )
    affiliation = forms.CharField(
        label='Affiliation',
        required=True,
        max_length=AFFILIATION_MAX_LENGTH,
        widget=forms.Textarea(attrs={'cols': '50', 'rows': '3'}),
        help_text=AFFILIATION_HELP
    )
    privacy_accepted = forms.BooleanField(
        label='I accept the privacy statement',
        required=True,
        error_messages={'required': 'You must accept the privacy statement to register'}
    )

    def clean_email(self):
        from vald.models import UserEmail
        email = self.cleaned_data['email'].lower()

        # Check if email already registered
        if UserEmail.objects.filter(email=email).exists():
            raise ValidationError(
                "This email address is already registered. Please use the login form or contact the administrator."
            )

        if '@' not in email:
            raise ValidationError("Your email address should at least contain a '@'!")

        return email


class AccountDetailsForm(forms.ModelForm):
    """Lets the account holder correct the affiliation stored about them.

    Affiliation only. `name` feeds User.client_name and therefore the output
    filenames the Fortran binaries write, and the email address is the delivery
    target resolved at job completion - neither is safe to change from here.
    """
    affiliation = forms.CharField(
        label='Affiliation',
        required=True,
        max_length=AFFILIATION_MAX_LENGTH,
        widget=forms.Textarea(attrs={'cols': '60', 'rows': '4'}),
        help_text=AFFILIATION_HELP
    )

    class Meta:
        model = User
        fields = ['affiliation']


class ExtractAllForm(UnitFieldsMixin, LinelistConfigChoiceMixin, forms.Form):
    """Extract All form"""
    stwvl = forms.FloatField(
        label='Starting wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    endwvl = forms.FloatField(
        label='Ending wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    format = forms.ChoiceField(
        label='Extraction format',
        choices=EXTRACTION_FORMAT_CHOICES,
        initial=EXTRACTION_FORMAT_DEFAULT,
        widget=forms.RadioSelect
    )
    email_notify = forms.BooleanField(
        label='Email me when the request finishes',
        required=False,
    )
    hfssplit = forms.BooleanField(
        label='Include HFS splitting',
        required=False
    )
    hrad = forms.BooleanField(
        label='Radiative damping constant',
        required=False
    )
    hstark = forms.BooleanField(
        label='Stark damping constant',
        required=False
    )
    hwaals = forms.BooleanField(
        label='Van der Waals damping constant',
        required=False
    )
    hlande = forms.BooleanField(
        label='Landé factor',
        required=False
    )
    hterm = forms.BooleanField(
        label='Term designation',
        required=False
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[],  # filled in per instance by LinelistConfigChoiceMixin
        initial='default',
        widget=ChoiceDisablingSelect
    )
    subject = forms.CharField(
        label='Optional comment for request',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'size': '40'})
    )

    def clean(self):
        cleaned_data = super().clean()
        stwvl = cleaned_data.get('stwvl')
        endwvl = cleaned_data.get('endwvl')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

        return cleaned_data


class ExtractElementForm(UnitFieldsMixin, LinelistConfigChoiceMixin, forms.Form):
    """Extract Element form"""
    stwvl = forms.FloatField(
        label='Starting wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    endwvl = forms.FloatField(
        label='Ending wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    elmion = forms.CharField(
        label='Element [ + ionization ]',
        required=True,
        max_length=20,
        widget=forms.TextInput(attrs={'size': '5'})
    )
    format = forms.ChoiceField(
        label='Extraction format',
        choices=EXTRACTION_FORMAT_CHOICES,
        initial=EXTRACTION_FORMAT_DEFAULT,
        widget=forms.RadioSelect
    )
    email_notify = forms.BooleanField(
        label='Email me when the request finishes',
        required=False,
    )
    hfssplit = forms.BooleanField(
        label='Include HFS splitting',
        required=False
    )
    hrad = forms.BooleanField(
        label='Radiative damping constant',
        required=False
    )
    hstark = forms.BooleanField(
        label='Stark damping constant',
        required=False
    )
    hwaals = forms.BooleanField(
        label='Van der Waals damping constant',
        required=False
    )
    hlande = forms.BooleanField(
        label='Landé factor',
        required=False
    )
    hterm = forms.BooleanField(
        label='Term designation',
        required=False
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[],  # filled in per instance by LinelistConfigChoiceMixin
        initial='default',
        widget=ChoiceDisablingSelect
    )
    subject = forms.CharField(
        label='Optional comment for request',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'size': '40'})
    )

    def clean_elmion(self):
        return clean_element_ionization(
            self.cleaned_data['elmion'], self.fields['elmion'].label
        )

    def clean(self):
        cleaned_data = super().clean()
        stwvl = cleaned_data.get('stwvl')
        endwvl = cleaned_data.get('endwvl')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

        return cleaned_data


class ExtractStellarForm(UnitFieldsMixin, LinelistConfigChoiceMixin, forms.Form):
    """Extract Stellar form"""
    stwvl = forms.FloatField(
        label='Starting wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    endwvl = forms.FloatField(
        label='Ending wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    dlimit = forms.FloatField(
        label='Detection threshold',
        required=True,
        min_value=0.0,
        max_value=1.0,
        widget=forms.TextInput(attrs={'size': '5'})
    )
    micturb = forms.FloatField(
        label='Microturbulence',
        required=True,
        min_value=0.0,
        widget=forms.TextInput(attrs={'size': '5'}),
        help_text='km/sec'
    )
    teff = forms.FloatField(
        label='Effective temperature',
        required=True,
        min_value=0.0,
        widget=forms.TextInput(attrs={'size': '5'}),
        help_text='K'
    )
    logg = forms.FloatField(
        label='Surface gravity',
        required=True,
        widget=forms.TextInput(attrs={'size': '5'}),
        help_text='log g in cgs units'
    )
    chemcomp = forms.CharField(
        label='Chemical composition',
        required=False,
        max_length=4000,
        widget=forms.Textarea(attrs={'rows': '2', 'cols': '50'}),
        # The metallicity hint is restored from the PHP form
        # (old/interface/extractstellar.html), which advertised it beside the
        # box. It was reachable but undocumented here: only the validation
        # error mentioned it, so you had to guess wrong to find out.
        help_text='optional, e.g. Sr: -4.67, Cr: -3.37 - solar values are used '
                  'for anything omitted. M/H: -0.5 sets an overall metallicity'
    )
    format = forms.ChoiceField(
        label='Extraction format',
        choices=EXTRACTION_FORMAT_CHOICES,
        initial=EXTRACTION_FORMAT_DEFAULT,
        widget=forms.RadioSelect
    )
    email_notify = forms.BooleanField(
        label='Email me when the request finishes',
        required=False,
    )
    hfssplit = forms.BooleanField(
        label='Include HFS splitting',
        required=False
    )
    hrad = forms.BooleanField(
        label='Radiative damping constant',
        required=False
    )
    hstark = forms.BooleanField(
        label='Stark damping constant',
        required=False
    )
    hwaals = forms.BooleanField(
        label='Van der Waals damping constant',
        required=False
    )
    hlande = forms.BooleanField(
        label='Landé factor',
        required=False
    )
    hterm = forms.BooleanField(
        label='Term designation',
        required=False
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[],  # filled in per instance by LinelistConfigChoiceMixin
        initial='default',
        widget=ChoiceDisablingSelect
    )
    subject = forms.CharField(
        label='Optional comment for request',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'size': '40'})
    )

    def clean_chemcomp(self):
        return clean_chemical_composition(self.cleaned_data['chemcomp'])

    def clean(self):
        cleaned_data = super().clean()
        stwvl = cleaned_data.get('stwvl')
        endwvl = cleaned_data.get('endwvl')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

        return cleaned_data


class ShowLineForm(LinelistConfigChoiceMixin, forms.Form):
    """Show Line form - 5 sets of wavelength/window/element fields"""
    # Set 0
    wvl0 = forms.FloatField(label='Approximate wavelength', required=False, min_value=0.01, widget=forms.TextInput(attrs={'size': '10'}))
    win0 = forms.FloatField(label='Wavelength window', required=False, min_value=0.01, max_value=5.0, widget=forms.TextInput(attrs={'size': '10'}))
    el0 = forms.CharField(label='Element [ + ionization ]', required=False, max_length=20, widget=forms.TextInput(attrs={'size': '5'}))

    # Set 1
    wvl1 = forms.FloatField(label='Approximate wavelength', required=False, min_value=0.01, widget=forms.TextInput(attrs={'size': '10'}))
    win1 = forms.FloatField(label='Wavelength window', required=False, min_value=0.01, max_value=5.0, widget=forms.TextInput(attrs={'size': '10'}))
    el1 = forms.CharField(label='Element [ + ionization ]', required=False, max_length=20, widget=forms.TextInput(attrs={'size': '5'}))

    # Set 2
    wvl2 = forms.FloatField(label='Approximate wavelength', required=False, min_value=0.01, widget=forms.TextInput(attrs={'size': '10'}))
    win2 = forms.FloatField(label='Wavelength window', required=False, min_value=0.01, max_value=5.0, widget=forms.TextInput(attrs={'size': '10'}))
    el2 = forms.CharField(label='Element [ + ionization ]', required=False, max_length=20, widget=forms.TextInput(attrs={'size': '5'}))

    # Set 3
    wvl3 = forms.FloatField(label='Approximate wavelength', required=False, min_value=0.01, widget=forms.TextInput(attrs={'size': '10'}))
    win3 = forms.FloatField(label='Wavelength window', required=False, min_value=0.01, max_value=5.0, widget=forms.TextInput(attrs={'size': '10'}))
    el3 = forms.CharField(label='Element [ + ionization ]', required=False, max_length=20, widget=forms.TextInput(attrs={'size': '5'}))

    # Set 4
    wvl4 = forms.FloatField(label='Approximate wavelength', required=False, min_value=0.01, widget=forms.TextInput(attrs={'size': '10'}))
    win4 = forms.FloatField(label='Wavelength window', required=False, min_value=0.01, max_value=5.0, widget=forms.TextInput(attrs={'size': '10'}))
    el4 = forms.CharField(label='Element [ + ionization ]', required=False, max_length=20, widget=forms.TextInput(attrs={'size': '5'}))

    email_notify = forms.BooleanField(
        label='Email me when the request finishes',
        required=False,
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[],  # filled in per instance by LinelistConfigChoiceMixin
        initial='default',
        widget=ChoiceDisablingSelect
    )
    isotopic_scaling = forms.ChoiceField(
        label='Isotopic scaling of oscillator strength',
        choices=[('on', 'On'), ('off', 'Off')],
        initial='on',
        widget=forms.RadioSelect
    )
    hfssplit = forms.BooleanField(
        label='Include HFS splitting',
        required=False
    )
    subject = forms.CharField(
        label='Optional comment for request',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'size': '40'})
    )

    def clean(self):
        cleaned_data = super().clean()

        # Check that at least one set is filled
        has_data = False
        for i in range(5):
            wvl = cleaned_data.get(f'wvl{i}')
            win = cleaned_data.get(f'win{i}')
            el = cleaned_data.get(f'el{i}')

            # If any field in the set is filled, all must be filled
            if wvl or win or el:
                has_data = True

                if not wvl:
                    raise ValidationError(f"Set {i}: Please enter a value in the 'Approximate wavelength' field")
                if not win:
                    raise ValidationError(f"Set {i}: Please enter a value in the 'Wavelength window' field")
                if not el:
                    raise ValidationError(f"Set {i}: Please enter a value in the 'Element + ionization' field")

                # Validate element + ionization format
                try:
                    cleaned_data[f'el{i}'] = clean_element_ionization(
                        el, self.fields[f'el{i}'].label
                    )
                except ValidationError as e:
                    raise ValidationError(f"Set {i}: {e.messages[0]}")

        if not has_data:
            raise ValidationError("Please fill in at least one complete set of wavelength/window/element")

        return cleaned_data


class ContactForm(forms.Form):
    """Contact form for general inquiries"""
    contactemail = forms.EmailField(
        label='Your email',
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'})
    )
    manager = forms.ChoiceField(
        label='To',
        choices=[
            ('valdadministrator', 'VALD Administrator (questions, general issues, support)'),
            ('webmaster', 'Webmaster (issues with this site)')
        ],
        initial='valdadministrator'
    )
    message = forms.CharField(
        label='Your message',
        required=True,
        widget=forms.Textarea(attrs={'cols': '50', 'rows': '8'})
    )
    permission = forms.BooleanField(
        label='I accept the privacy statement',
        required=True,
        error_messages={'required': 'Please check accept the conditions stated in the form'}
    )
    privacy_statement = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
        initial="""By submitting a request for information or registration through the form
above, you give us permission to process and store your personal data to
comply with your request. In case of registration, your name, email address
and affiliation will be stored on our servers, which are distributed around
the world."""
    )

    def clean_contactemail(self):
        email = self.cleaned_data['contactemail']
        if '@' not in email:
            raise ValidationError("Your email address should at least contain a '@'!")
        return email


class ShowLineOnlineForm(LinelistConfigChoiceMixin, forms.Form):
    """Show Line form - single wavelength/window/element set"""
    wvl0 = forms.FloatField(
        label='Approximate wavelength',
        required=True,
        min_value=0.01,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    win0 = forms.FloatField(
        label='Wavelength window',
        required=True,
        min_value=0.01,
        max_value=5.0,
        widget=forms.TextInput(attrs={'size': '10'})
    )
    el0 = forms.CharField(
        label='Element [ + ionization ]',
        required=True,
        max_length=20,
        widget=forms.TextInput(attrs={'size': '5'})
    )
    email_notify = forms.BooleanField(
        label='Email me when the request finishes',
        required=False,
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[],  # filled in per instance by LinelistConfigChoiceMixin
        initial='default',
        widget=ChoiceDisablingSelect
    )
    isotopic_scaling = forms.ChoiceField(
        label='Isotopic scaling of oscillator strength',
        choices=[('on', 'On'), ('off', 'Off')],
        initial='on',
        widget=forms.RadioSelect
    )

    def clean_el0(self):
        return clean_element_ionization(
            self.cleaned_data['el0'], self.fields['el0'].label
        )
