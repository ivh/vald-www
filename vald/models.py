from django.db import models


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
