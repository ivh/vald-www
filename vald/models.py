from django.db import models
from django.contrib.auth.hashers import make_password, check_password
from pathlib import Path
import secrets
import uuid


class Request(models.Model):
    """Track all extraction/query requests - works for all request types"""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('complete', 'Complete'),
        ('failed', 'Failed'),
    ]

    # Unique identifier for URLs
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    # User reference (nullable for migration, but required in practice)
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='requests', null=True)
    request_type = models.CharField(max_length=20, db_index=True)  # extractall, extractelement, etc.

    # All request parameters stored as JSON (flexible for different request types)
    parameters = models.JSONField()

    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    queue_position = models.IntegerField(null=True, blank=True)  # Position in queue (optional)

    # Output file tracking (set by at-job when processing completes)
    output_file = models.CharField(max_length=500, blank=True, null=True)
    error_message = models.TextField(blank=True, null=True)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Request"
        verbose_name_plural = "Requests"

    def __str__(self):
        user_display = self.user.name if self.user else 'Unknown'
        return f"{self.request_type} by {user_display} ({self.status})"

    @property
    def user_email(self):
        """Get primary email for the user (for email sending)"""
        return self.user.primary_email if self.user else None

    @property
    def user_name(self):
        """Get user name"""
        return self.user.name if self.user else 'Unknown'

    def is_complete(self):
        """Check if request has completed"""
        return self.status == 'complete'

    def is_failed(self):
        """Check if request has failed"""
        return self.status == 'failed'

    def is_pending(self):
        """Check if request is still pending"""
        return self.status in ['pending', 'processing']

    def output_exists(self):
        """Check if output file exists on filesystem"""
        if not self.output_file:
            return False
        return Path(self.output_file).exists()

    def get_output_size(self):
        """Get size of output file in human-readable format"""
        if not self.output_exists():
            return None
        size_bytes = Path(self.output_file).stat().st_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def get_bib_output_file(self):
        """Get path to .bib.gz file if it exists"""
        if not self.output_file:
            return None
        # Replace .gz with .bib.gz
        output_path = Path(self.output_file)
        if output_path.suffix == '.gz':
            bib_path = output_path.with_suffix('.bib.gz')
            return str(bib_path)
        return None

    def bib_output_exists(self):
        """Check if .bib.gz output file exists on filesystem"""
        bib_file = self.get_bib_output_file()
        if not bib_file:
            return False
        return Path(bib_file).exists()

    def get_bib_output_size(self):
        """Get size of .bib.gz output file in human-readable format"""
        if not self.bib_output_exists():
            return None
        bib_file = self.get_bib_output_file()
        size_bytes = Path(bib_file).stat().st_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"


class User(models.Model):
    """User model for authentication - supports multiple emails per user"""
    name = models.CharField(max_length=255)
    affiliation = models.TextField(blank=True)  # Free text from clients.register
    password = models.CharField(max_length=128, blank=True, null=True)  # Null = needs activation
    activation_token = models.CharField(max_length=64, blank=True, null=True, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"

    def __str__(self):
        return f"{self.name}"

    def set_password(self, raw_password):
        """Hash and set password"""
        self.password = make_password(raw_password)
        self.activation_token = None  # Clear activation token once password is set

    def check_password(self, raw_password):
        """Check if password matches"""
        if not self.password:
            return False
        return check_password(raw_password, self.password)

    def generate_activation_token(self):
        """Generate a unique activation token"""
        self.activation_token = secrets.token_urlsafe(32)
        return self.activation_token

    def needs_activation(self):
        """Check if user needs to set password"""
        return not self.password

    @property
    def client_name(self):
        """Get alphanumeric-only version of name for file paths"""
        return ''.join(c for c in self.name if c.isalnum())

    @property
    def primary_email(self):
        """Get the primary email address, or first email if none marked primary"""
        primary = self.emails.filter(is_primary=True).first()
        if primary:
            return primary.email
        first = self.emails.first()
        return first.email if first else None

    def get_preferences(self):
        """Get user preferences, creating defaults if none exist"""
        prefs, _ = UserPreferences.objects.get_or_create(user=self)
        return prefs


class UserEmail(models.Model):
    """Email addresses for users - supports multiple emails per user"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='emails')
    email = models.EmailField(unique=True, db_index=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Email"
        verbose_name_plural = "User Emails"

    def __str__(self):
        return f"{self.email} ({self.user.name})"


class UserPreferences(models.Model):
    """Store user unit preferences (energy unit, wavelength unit, medium, etc.)
    
    These preferences are used to pre-fill extraction forms and set the 
    appropriate flags in pres_in files for the backend.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    
    # Unit preferences - these map to pres_in flags
    ENERGY_CHOICES = [
        ('eV', 'eV'),
        ('cm', 'cm⁻¹'),
    ]
    MEDIUM_CHOICES = [
        ('air', 'Air (λ > 200nm)'),
        ('vacuum', 'Vacuum'),
    ]
    WAVEUNIT_CHOICES = [
        ('angstrom', 'Ångström'),
        ('nm', 'Nanometers'),
        ('cm', 'cm⁻¹'),
    ]
    VDWFORMAT_CHOICES = [
        ('default', 'Default (single value)'),
        ('extended', 'Extended format'),
    ]
    ISOTOPIC_CHOICES = [
        ('on', 'Apply isotopic scaling'),
        ('off', 'No isotopic scaling'),
    ]
    
    energyunit = models.CharField(max_length=10, choices=ENERGY_CHOICES, default='eV')
    medium = models.CharField(max_length=10, choices=MEDIUM_CHOICES, default='air')
    waveunit = models.CharField(max_length=10, choices=WAVEUNIT_CHOICES, default='angstrom')
    vdwformat = models.CharField(max_length=20, choices=VDWFORMAT_CHOICES, default='default')
    isotopic_scaling = models.CharField(max_length=10, choices=ISOTOPIC_CHOICES, default='on')
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Preferences"
        verbose_name_plural = "User Preferences"

    def __str__(self):
        return f"Preferences for {self.user.name}"
    
    def as_dict(self):
        """Return preferences as a dictionary for form prefilling"""
        return {
            'energyunit': self.energyunit,
            'medium': self.medium,
            'waveunit': self.waveunit,
            'vdwformat': self.vdwformat,
            'isotopic_scaling': self.isotopic_scaling,
        }


# ============================================================================
# DEPRECATED MODELS - File-based implementation only
# ============================================================================
# PersonalConfig and LineList models were used for database-backed
# configuration storage. Personal configs are now file-based.
# Models kept commented out for reference and migration history.
# ============================================================================

# class LineList(models.Model):
#     """Represents a single linelist in a personal configuration"""
#
#     # Foreign key to personal config
#     personal_config = models.ForeignKey('PersonalConfig', on_delete=models.CASCADE, related_name='linelists')
#
#     # Linelist properties
#     list_id = models.IntegerField()  # The identifier from the config file
#     name = models.CharField(max_length=255)
#     commented = models.BooleanField(default=False)  # True if deactivated
#
#     # Parameters (indexed from 0-14 as in PHP)
#     param_0 = models.CharField(max_length=255, blank=True)  # filename
#     param_1 = models.CharField(max_length=50, blank=True)   # list_id (duplicate)
#     param_2 = models.CharField(max_length=50, blank=True)
#     param_3 = models.CharField(max_length=50, blank=True)
#     param_4 = models.CharField(max_length=50, blank=True)
#     param_5 = models.CharField(max_length=50, blank=True)
#     param_6 = models.CharField(max_length=50, blank=True)
#     param_7 = models.CharField(max_length=50, blank=True)
#     param_8 = models.CharField(max_length=50, blank=True)
#     param_9 = models.CharField(max_length=50, blank=True)
#     param_10 = models.CharField(max_length=50, blank=True)
#     param_11 = models.CharField(max_length=50, blank=True)
#     param_12 = models.CharField(max_length=50, blank=True)
#     param_13 = models.CharField(max_length=50, blank=True)
#     param_14 = models.TextField(blank=True)  # name/description
#
#     # Flags for tracking modifications
#     mod_comment = models.BooleanField(default=False)
#     mod_param_1 = models.BooleanField(default=False)
#     mod_param_2 = models.BooleanField(default=False)
#     mod_param_3 = models.BooleanField(default=False)
#     mod_param_4 = models.BooleanField(default=False)
#     mod_param_5 = models.BooleanField(default=False)
#     mod_param_6 = models.BooleanField(default=False)
#     mod_param_7 = models.BooleanField(default=False)
#     mod_param_8 = models.BooleanField(default=False)
#     mod_param_9 = models.BooleanField(default=False)
#     mod_param_10 = models.BooleanField(default=False)
#     mod_param_11 = models.BooleanField(default=False)
#     mod_param_12 = models.BooleanField(default=False)
#     mod_param_13 = models.BooleanField(default=False)
#
#     class Meta:
#         ordering = ['list_id']
#
#     def __str__(self):
#         return f"LineList {self.list_id}: {self.name}"
#
#     def get_param(self, index):
#         """Get parameter by index (0-14)"""
#         return getattr(self, f'param_{index}', '')
#
#     def set_param(self, index, value):
#         """Set parameter by index (0-14)"""
#         setattr(self, f'param_{index}', value)
#
#     def get_mod_flag(self, index):
#         """Get modification flag by index"""
#         if index == 0:
#             return self.mod_comment
#         return getattr(self, f'mod_param_{index}', False)
#
#     def set_mod_flag(self, index, value):
#         """Set modification flag by index"""
#         if index == 0:
#             self.mod_comment = value
#         else:
#             setattr(self, f'mod_param_{index}', value)
#
#
# class PersonalConfig(models.Model):
#     """Manages the complete personal configuration for a user"""
#
#     email = models.EmailField(unique=True, db_index=True)
#
#     # Hidden parameters (first 4 values from config file)
#     hidden_param_0 = models.CharField(max_length=255, blank=True)
#     hidden_param_1 = models.CharField(max_length=255, blank=True)
#     hidden_param_2 = models.CharField(max_length=255, blank=True)
#     hidden_param_3 = models.CharField(max_length=255, blank=True)
#
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)
#
#     def __str__(self):
#         return f"PersonalConfig for {self.email}"
#
#     def get_linelist_by_id(self, list_id):
#         """Find a linelist by its ID"""
#         try:
#             return self.linelists.get(list_id=list_id)
#         except LineList.DoesNotExist:
#             return None
