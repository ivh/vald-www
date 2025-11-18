from django import forms
from django.core.exceptions import ValidationError
import re


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
        choices=[('email', 'Email'), ('via ftp', 'FTP')],
        initial='email',
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
                    "The maximum wavelength range that can be requested by email is 50 Å. Select FTP method!"
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
        choices=[('email', 'Email'), ('via ftp', 'FTP')],
        initial='email',
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
        elmion = self.cleaned_data['elmion'].strip()
        parts = elmion.split()

        if len(parts) > 1:
            # Check if ionization stage is a number
            ionization = parts[1]
            if ionization and not ionization.isdigit():
                raise ValidationError(
                    "Please express the ionization stage as an arabic number"
                )

        return elmion

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
                    "The maximum wavelength range that can be requested by email is 50 Å. Select FTP method!"
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
        widget=forms.Textarea(attrs={'rows': '2', 'cols': '50'})
    )
    format = forms.ChoiceField(
        label='Extraction format',
        choices=[('short', 'Short format'), ('long', 'Long format')],
        initial='short',
        widget=forms.RadioSelect
    )
    viaftp = forms.ChoiceField(
        label='Retrieve data via',
        choices=[('email', 'Email'), ('via ftp', 'FTP')],
        initial='email',
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
                    "The maximum wavelength range that can be requested by email is 50 Å. Select FTP method!"
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

    def _validate_element_ionization(self, element_str):
        """Validate element + ionization format"""
        if not element_str:
            return True

        parts = element_str.split()
        if len(parts) > 1:
            ionization = parts[1]
            if ionization and not ionization.isdigit():
                return False
        return True

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
                if not self._validate_element_ionization(el):
                    raise ValidationError(f"Set {i}: Please express the ionization stage as an arabic number")

        if not has_data:
            raise ValidationError("Please fill in at least one complete set of wavelength/window/element")

        return cleaned_data


class ContactForm(forms.Form):
    """Contact/Registration form"""
    contactemail = forms.EmailField(
        label='Your email',
        required=True,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'})
    )
    contactname = forms.CharField(
        label='Full name',
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'}),
        help_text='required for registration'
    )
    affiliation = forms.CharField(
        label='Affiliation',
        required=False,
        max_length=200,
        widget=forms.TextInput(attrs={'size': '50'}),
        help_text='required for registration'
    )
    position = forms.CharField(
        label='Current position',
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={'size': '50'}),
        help_text='for statistics only'
    )
    manager = forms.ChoiceField(
        label='To',
        choices=[
            ('valdadministrator', 'VALD Administrator (registration, questions, general issues, support)'),
            ('valdwebmanager', 'VALD Web manager (issues concerning this mirror)')
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
    """Show Line Online form - single wavelength/window/element set for immediate execution"""
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
        el = self.cleaned_data['el0'].strip()
        parts = el.split()

        if len(parts) > 1:
            # Check if ionization stage is a number
            ionization = parts[1]
            if ionization and not ionization.isdigit():
                raise ValidationError(
                    "Please express the ionization stage as an arabic number"
                )

        return el
