from django import forms
from django.core.exceptions import ValidationError
import re


# These two values are written verbatim into the control files the Fortran
# binaries read (pres_in/show_in line 3, select.input before the 'END'
# sentinel), where a stray newline shifts every following line - which would
# let a request choose its own config path or select's output filename.
# Validating the shape here also turns a typo into a form error instead of an
# opaque failure inside preselect5/select5.

# <element> [spectral number], allowing an isotope prefix (48Ca 2) and
# molecules (TiO, H2O). Deliberately permissive about the species itself.
ELEMENT_ION_RE = re.compile(r'^\d{0,3}[A-Za-z][A-Za-z0-9]{0,5}(?: \d{1,2})?$')

# One abundance pair: "Fe: -4.50", "H :  0.91" (documentation/reqextstar.html)
ABUNDANCE_PAIR_RE = re.compile(r'^([A-Za-z]{1,2})\s*:\s*([+-]?\d+(?:\.\d+)?)$')

MAX_ABUNDANCE_PAIRS = 200


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
    """Validate 'element: log abundance' pairs, preserving the user's layout."""
    lines = [line.strip() for line in value.splitlines()]
    lines = [line for line in lines if line]

    pairs = 0
    for line in lines:
        for token in line.split(','):
            token = token.strip()
            if not token:
                continue
            if not ABUNDANCE_PAIR_RE.match(token):
                raise ValidationError(
                    f'Could not read "{token}" as a chemical composition entry. '
                    'Give each element as <element>: <log abundance> - for example '
                    '"Fe: -4.50" - separating several entries with commas.'
                )
            pairs += 1

    if pairs > MAX_ABUNDANCE_PAIRS:
        raise ValidationError(
            f'At most {MAX_ABUNDANCE_PAIRS} abundance entries can be given '
            f'({pairs} found).'
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

        if password and len(password) < 6:
            raise ValidationError("Password must be at least 6 characters long.")

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
        max_length=200,
        widget=forms.TextInput(attrs={'size': '50'})
    )
    position = forms.CharField(
        label='Current position',
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'}),
        help_text='optional, for statistics only'
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


class ExtractAllForm(forms.Form):
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
        choices=[('short', 'Short format'), ('long', 'Long format')],
        initial='short',
        widget=forms.RadioSelect
    )
    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('via ftp', 'Download'), ('email', 'Email')],
        initial='via ftp',
        widget=forms.RadioSelect
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
        choices=[('default', 'Default'), ('personal', 'Custom')],
        initial='default',
        widget=forms.RadioSelect
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
        viaftp = cleaned_data.get('viaftp')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

            # Check wavelength range limit for email delivery
            if (endwvl - stwvl) > 50 and viaftp != 'via ftp':
                raise ValidationError(
                    "The maximum wavelength range that can be requested by email is 50 Å. Select Download method!"
                )

        return cleaned_data


class ExtractElementForm(forms.Form):
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
        choices=[('short', 'Short format'), ('long', 'Long format')],
        initial='short',
        widget=forms.RadioSelect
    )
    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('via ftp', 'Download'), ('email', 'Email')],
        initial='via ftp',
        widget=forms.RadioSelect
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
        choices=[('default', 'Default'), ('personal', 'Custom')],
        initial='default',
        widget=forms.RadioSelect
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
        viaftp = cleaned_data.get('viaftp')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

            # Check wavelength range limit for email delivery
            if (endwvl - stwvl) > 50 and viaftp != 'via ftp':
                raise ValidationError(
                    "The maximum wavelength range that can be requested by email is 50 Å. Select Download method!"
                )

        return cleaned_data


class ExtractStellarForm(forms.Form):
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
        help_text='optional, e.g. Sr: -4.67, Cr: -3.37 - solar values are used for anything omitted'
    )
    format = forms.ChoiceField(
        label='Extraction format',
        choices=[('short', 'Short format'), ('long', 'Long format')],
        initial='short',
        widget=forms.RadioSelect
    )
    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('via ftp', 'Download'), ('email', 'Email')],
        initial='via ftp',
        widget=forms.RadioSelect
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
        choices=[('default', 'Default'), ('personal', 'Custom')],
        initial='default',
        widget=forms.RadioSelect
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
        viaftp = cleaned_data.get('viaftp')

        if stwvl and endwvl:
            if endwvl <= stwvl:
                raise ValidationError(
                    "The 'Ending wavelength' cannot be smaller than or equal to the 'Starting wavelength'"
                )

            # Check wavelength range limit for email delivery
            if (endwvl - stwvl) > 50 and viaftp != 'via ftp':
                raise ValidationError(
                    "The maximum wavelength range that can be requested by email is 50 Å. Select Download method!"
                )

        return cleaned_data


class ShowLineForm(forms.Form):
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

    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('via ftp', 'Download'), ('email', 'Email')],
        initial='via ftp',
        widget=forms.RadioSelect
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[('default', 'Default'), ('personal', 'Custom')],
        initial='default',
        widget=forms.RadioSelect
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


class ShowLineOnlineForm(forms.Form):
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
    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('via ftp', 'Download'), ('email', 'Email')],
        initial='via ftp',
        widget=forms.RadioSelect
    )
    pconf = forms.ChoiceField(
        label='Linelist configuration',
        choices=[('default', 'Default'), ('personal', 'Custom')],
        initial='default',
        widget=forms.RadioSelect
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
