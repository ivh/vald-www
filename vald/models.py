from django.db import models
from django.contrib.auth.hashers import make_password, check_password
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

    # User and request info
    user_email = models.EmailField(db_index=True)
    user_name = models.CharField(max_length=255)
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
        return f"{self.request_type} by {self.user_email} ({self.status})"

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
        from pathlib import Path
        return Path(self.output_file).exists()

    def get_output_size(self):
        """Get size of output file in human-readable format"""
        if not self.output_exists():
            return None
        from pathlib import Path
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
        from pathlib import Path
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
        from pathlib import Path
        return Path(bib_file).exists()

    def get_bib_output_size(self):
        """Get size of .bib.gz output file in human-readable format"""
        if not self.bib_output_exists():
            return None
        from pathlib import Path
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
    """Store user HTML defaults (energy unit, wavelength unit, medium, etc.)"""
    email = models.EmailField(unique=True, db_index=True)
    name = models.CharField(max_length=255)

    # Unit preferences
    energyunit = models.CharField(max_length=10, default='eV')
    medium = models.CharField(max_length=10, default='air')
    waveunit = models.CharField(max_length=10, default='angstrom')
    vdwformat = models.CharField(max_length=20, default='default')
    isotopic_scaling = models.CharField(max_length=10, default='on')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "User Preferences"

    def __str__(self):
        return f"{self.name} ({self.email})"


class LineList(models.Model):
    """Represents a single linelist in a personal configuration"""

    # Foreign key to personal config
    personal_config = models.ForeignKey('PersonalConfig', on_delete=models.CASCADE, related_name='linelists')

    # Linelist properties
    list_id = models.IntegerField()  # The identifier from the config file
    name = models.CharField(max_length=255)
    commented = models.BooleanField(default=False)  # True if deactivated

    # Parameters (indexed from 0-14 as in PHP)
    param_0 = models.CharField(max_length=255, blank=True)  # filename
    param_1 = models.CharField(max_length=50, blank=True)   # list_id (duplicate)
    param_2 = models.CharField(max_length=50, blank=True)
    param_3 = models.CharField(max_length=50, blank=True)
    param_4 = models.CharField(max_length=50, blank=True)
    param_5 = models.CharField(max_length=50, blank=True)
    param_6 = models.CharField(max_length=50, blank=True)
    param_7 = models.CharField(max_length=50, blank=True)
    param_8 = models.CharField(max_length=50, blank=True)
    param_9 = models.CharField(max_length=50, blank=True)
    param_10 = models.CharField(max_length=50, blank=True)
    param_11 = models.CharField(max_length=50, blank=True)
    param_12 = models.CharField(max_length=50, blank=True)
    param_13 = models.CharField(max_length=50, blank=True)
    param_14 = models.TextField(blank=True)  # name/description

    # Flags for tracking modifications
    mod_comment = models.BooleanField(default=False)
    mod_param_1 = models.BooleanField(default=False)
    mod_param_2 = models.BooleanField(default=False)
    mod_param_3 = models.BooleanField(default=False)
    mod_param_4 = models.BooleanField(default=False)
    mod_param_5 = models.BooleanField(default=False)
    mod_param_6 = models.BooleanField(default=False)
    mod_param_7 = models.BooleanField(default=False)
    mod_param_8 = models.BooleanField(default=False)
    mod_param_9 = models.BooleanField(default=False)
    mod_param_10 = models.BooleanField(default=False)
    mod_param_11 = models.BooleanField(default=False)
    mod_param_12 = models.BooleanField(default=False)
    mod_param_13 = models.BooleanField(default=False)

    class Meta:
        ordering = ['list_id']

    def __str__(self):
        return f"LineList {self.list_id}: {self.name}"

    def get_param(self, index):
        """Get parameter by index (0-14)"""
        return getattr(self, f'param_{index}', '')

    def set_param(self, index, value):
        """Set parameter by index (0-14)"""
        setattr(self, f'param_{index}', value)

    def get_mod_flag(self, index):
        """Get modification flag by index"""
        if index == 0:
            return self.mod_comment
        return getattr(self, f'mod_param_{index}', False)

    def set_mod_flag(self, index, value):
        """Set modification flag by index"""
        if index == 0:
            self.mod_comment = value
        else:
            setattr(self, f'mod_param_{index}', value)


class PersonalConfig(models.Model):
    """Manages the complete personal configuration for a user"""

    email = models.EmailField(unique=True, db_index=True)

    # Hidden parameters (first 4 values from config file)
    hidden_param_0 = models.CharField(max_length=255, blank=True)
    hidden_param_1 = models.CharField(max_length=255, blank=True)
    hidden_param_2 = models.CharField(max_length=255, blank=True)
    hidden_param_3 = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PersonalConfig for {self.email}"

    def get_linelist_by_id(self, list_id):
        """Find a linelist by its ID"""
        try:
            return self.linelists.get(list_id=list_id)
        except LineList.DoesNotExist:
            return None
