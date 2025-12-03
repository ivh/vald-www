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
        name = ''.join(c for c in self.name if c.isalnum())
        if not name:
            # Fallback for names with only special chars (e.g., Chinese, Arabic, symbols)
            return f"user{self.id}"
        return name

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
        ('1/cm', 'cm⁻¹'),
    ]
    MEDIUM_CHOICES = [
        ('air', 'Air (λ > 200nm)'),
        ('vacuum', 'Vacuum'),
    ]
    WAVEUNIT_CHOICES = [
        ('angstrom', 'Ångström'),
        ('nm', 'Nanometers'),
        ('1/cm', 'cm⁻¹'),
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


# ============================================================================
# LINELIST CONFIGURATION MODELS
# ============================================================================
# These models replace the file-based .cfg configuration system.
# Each user can have their own Config with customized linelist selection.
# ============================================================================

class Linelist(models.Model):
    """
    Master catalog of available linelists in the VALD database.
    
    Each linelist represents a data source (file) containing spectral line data.
    Linelists have element ranges and quality rankings for different parameters.
    """
    # Path to the binary data file (relative to VALD data root)
    path = models.CharField(max_length=255, unique=True, 
                           help_text="Path to linelist file, e.g., '/CVALD3/ATOMS/Fe_NBS_cut_V3'")
    
    # Human-readable name
    name = models.CharField(max_length=200, 
                           help_text="Description, e.g., 'Fe: NBS data'")
    
    # Element range (using VALD element codes)
    # 1=H, 2=He, ... 326=Fe I, 327=Fe II, etc.
    element_min = models.IntegerField(help_text="Minimum element code (e.g., 326 for Fe I)")
    element_max = models.IntegerField(help_text="Maximum element code (e.g., 334 for Fe IX)")
    
    # Classification
    is_molecular = models.BooleanField(default=False)
    source = models.CharField(max_length=100, blank=True, 
                             help_text="Data source (NBS, Kurucz, NIST, etc.)")
    
    # Default priority (lower = higher priority in merging)
    default_priority = models.IntegerField(default=1000)
    
    # Default rank weights (1-9, higher = better quality)
    # These are used when merging duplicate lines from multiple sources
    default_rank_wl = models.IntegerField(default=3, help_text="Wavelength quality rank (1-9)")
    default_rank_gf = models.IntegerField(default=3, help_text="Oscillator strength quality rank")
    default_rank_rad = models.IntegerField(default=3, help_text="Radiative damping quality rank")
    default_rank_stark = models.IntegerField(default=3, help_text="Stark damping quality rank")
    default_rank_waals = models.IntegerField(default=3, help_text="Van der Waals damping quality rank")
    default_rank_lande = models.IntegerField(default=3, help_text="Lande factor quality rank")
    default_rank_term = models.IntegerField(default=3, help_text="Term designation quality rank")
    default_rank_ext_vdw = models.IntegerField(default=3, help_text="Extended VdW quality rank")
    default_rank_zeeman = models.IntegerField(default=3, help_text="Zeeman data quality rank")
    
    # Metadata
    is_active = models.BooleanField(default=True, help_text="Whether this linelist is currently available")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Linelist"
        verbose_name_plural = "Linelists"
        ordering = ['default_priority', 'path']
    
    def __str__(self):
        return f"{self.name} ({self.path})"


class Config(models.Model):
    """
    A configuration set defining which linelists to use and their settings.
    
    Users can have personal configs that customize the default selection.
    The system default config (user=NULL, is_default=True) is used when
    no personal config is specified.
    """
    name = models.CharField(max_length=100, help_text="Configuration name")
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True,
                            related_name='configs',
                            help_text="Owner (NULL = system config)")
    is_default = models.BooleanField(default=False,
                                     help_text="Whether this is the default config for this user")
    
    # Global parameters (line 1 of .cfg file)
    wl_window_ref = models.FloatField(default=0.05,
                                      help_text="Wavelength window reference (Å)")
    wl_ref = models.FloatField(default=5000.0,
                               help_text="Reference wavelength (Å)")
    max_ionization = models.IntegerField(default=9,
                                         help_text="Maximum ionization stage")
    max_excitation_eV = models.FloatField(default=150.0,
                                          help_text="Maximum excitation potential (eV)")
    
    # Many-to-many relationship with linelists through ConfigLinelist
    linelists = models.ManyToManyField(Linelist, through='ConfigLinelist',
                                       related_name='configs')
    
    # Metadata
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Configuration"
        verbose_name_plural = "Configurations"
        ordering = ['user', 'name']
        # Ensure only one default config per user (or system)
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'is_default'],
                condition=models.Q(is_default=True),
                name='unique_default_config_per_user'
            )
        ]
    
    def __str__(self):
        owner = self.user.name if self.user else "System"
        default_marker = " (default)" if self.is_default else ""
        return f"{owner}: {self.name}{default_marker}"
    
    def generate_cfg_content(self):
        """
        Generate the .cfg file content from this configuration.
        
        Returns:
            str: Content of the .cfg file for use with Fortran preselect
        """
        lines = []
        
        # Line 1: global parameters
        # Format: wl_window,wl_ref.,max_ion,max_exc.
        lines.append(f"{self.wl_window_ref},{self.wl_ref:.0f}.,{self.max_ionization},{self.max_excitation_eV:.0f}.")
        
        # Linelist lines (sorted by priority)
        for cl in self.configlinelist_set.select_related('linelist').order_by('priority'):
            if not cl.is_enabled:
                prefix = ";"  # Comment out disabled linelists
            else:
                prefix = ""
            
            # Build rank weights string
            ranks = f"{cl.rank_wl},{cl.rank_gf},{cl.rank_rad},{cl.rank_stark},"
            ranks += f"{cl.rank_waals},{cl.rank_lande},{cl.rank_term},"
            ranks += f"{cl.rank_ext_vdw},{cl.rank_zeeman}"
            
            # Format: 'path', priority, elem_min, elem_max, mergeable, ranks, 'name'
            line = f"{prefix}'{cl.linelist.path}', {cl.priority}, "
            line += f"{cl.linelist.element_min}, {cl.linelist.element_max}, "
            line += f"{cl.mergeable}, {ranks}, '{cl.linelist.name}'"
            
            # Add replacement window for mergeable=2 (replacement lists) or if non-default
            if cl.mergeable == 2 or cl.replacement_window != 0.05:
                line += f", {cl.replacement_window}"
            
            lines.append(line)
        
        return '\n'.join(lines)
    
    @classmethod
    def get_default_config(cls):
        """Get the system default configuration."""
        return cls.objects.filter(user__isnull=True, is_default=True).first()
    
    @classmethod
    def get_user_config(cls, user):
        """Get the user's default config, falling back to system default."""
        if user:
            user_config = cls.objects.filter(user=user, is_default=True).first()
            if user_config:
                return user_config
        return cls.get_default_config()


class ConfigLinelist(models.Model):
    """
    Junction table defining which linelists are in a config and their settings.
    
    This allows per-config customization of priority, rank weights, and enabled status.
    """
    config = models.ForeignKey(Config, on_delete=models.CASCADE)
    linelist = models.ForeignKey(Linelist, on_delete=models.CASCADE)
    
    # Per-config settings
    priority = models.IntegerField(help_text="Read order (lower = higher priority)")
    is_enabled = models.BooleanField(default=True,
                                     help_text="Whether this linelist is active (False = commented out)")
    
    # Mergeable flag
    MERGEABLE_CHOICES = [
        (0, 'Mergeable'),
        (1, 'Standalone (never merge)'),
        (2, 'Replacement list (always merge)'),
    ]
    mergeable = models.IntegerField(choices=MERGEABLE_CHOICES, default=0)
    replacement_window = models.FloatField(default=0.05,
                                           help_text="Wavelength tolerance for merging (Å)")
    
    # Override rank weights (if different from linelist defaults)
    rank_wl = models.IntegerField(default=3)
    rank_gf = models.IntegerField(default=3)
    rank_rad = models.IntegerField(default=3)
    rank_stark = models.IntegerField(default=3)
    rank_waals = models.IntegerField(default=3)
    rank_lande = models.IntegerField(default=3)
    rank_term = models.IntegerField(default=3)
    rank_ext_vdw = models.IntegerField(default=3)
    rank_zeeman = models.IntegerField(default=3)
    
    class Meta:
        verbose_name = "Config Linelist"
        verbose_name_plural = "Config Linelists"
        ordering = ['priority']
        unique_together = ['config', 'linelist']
    
    def __str__(self):
        status = "" if self.is_enabled else " (disabled)"
        return f"{self.config.name}: {self.linelist.name} @ priority {self.priority}{status}"
    
    def save(self, *args, **kwargs):
        # If rank weights are default (3), inherit from linelist
        if not self.pk:  # New record
            ll = self.linelist
            if self.rank_wl == 3:
                self.rank_wl = ll.default_rank_wl
            if self.rank_gf == 3:
                self.rank_gf = ll.default_rank_gf
            if self.rank_rad == 3:
                self.rank_rad = ll.default_rank_rad
            if self.rank_stark == 3:
                self.rank_stark = ll.default_rank_stark
            if self.rank_waals == 3:
                self.rank_waals = ll.default_rank_waals
            if self.rank_lande == 3:
                self.rank_lande = ll.default_rank_lande
            if self.rank_term == 3:
                self.rank_term = ll.default_rank_term
            if self.rank_ext_vdw == 3:
                self.rank_ext_vdw = ll.default_rank_ext_vdw
            if self.rank_zeeman == 3:
                self.rank_zeeman = ll.default_rank_zeeman
        super().save(*args, **kwargs)
