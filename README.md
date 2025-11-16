# VALD Web Interface - Django Version

This is a Django replacement for the original PHP-based VALD (Vienna Atomic Line Database) web interface. It provides a drop-in replacement with the same functionality and form layouts.

## Features

- User authentication via email (file-based validation against client register)
- 5 main request forms:
  - Extract All
  - Extract Element
  - Extract Stellar
  - Show Line
  - Contact form (public access)
- User preferences (energy units, wavelength units, medium, etc.)
- Email submission of requests via SMTP
- Session-based authentication
- Re-uses original HTML structure and CSS

## Requirements

- Python 3.11+
- uv (for dependency management)
- SQLite (included with Python)
- SMTP server for sending emails

## Installation

1. **Create virtual environment:**
   ```bash
   uv venv
   ```

2. **Install dependencies:**
   ```bash
   uv pip install django
   ```

3. **Run migrations:**
   ```bash
   source .venv/bin/activate
   python manage.py migrate
   ```

4. **Configure email settings** (see Configuration section below)

5. **Set up client register files** (see Configuration section below)

## Running the Application

Using uv:
```bash
uv run manage.py runserver
```

Or with activated virtual environment:
```bash
source .venv/bin/activate
python manage.py runserver
```

The application will be available at http://127.0.0.1:8000/

## Configuration

### Email Settings

Edit `vald_web/settings.py` and configure the email backend:

```python
EMAIL_HOST = 'smtp.your-server.com'  # Your SMTP server
EMAIL_PORT = 587                      # SMTP port (usually 587 for TLS)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'your-email@example.com'      # SMTP username
EMAIL_HOST_PASSWORD = 'your-password'           # SMTP password
DEFAULT_FROM_EMAIL = 'noreply@vald.local'
VALD_REQUEST_EMAIL = 'vald-request@localhost'  # Where requests are sent
```

**Important:** For production, use environment variables instead of hardcoding credentials:

```python
import os

EMAIL_HOST = os.getenv('EMAIL_HOST', 'localhost')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', 587))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
```

Then set environment variables before running:
```bash
export EMAIL_HOST=smtp.gmail.com
export EMAIL_PORT=587
export EMAIL_HOST_USER=your-email@gmail.com
export EMAIL_HOST_PASSWORD=your-app-password
uv run manage.py runserver
```

### Client Register Files

User authentication is file-based. Edit these files to add registered users:

**`config/clients.register`** (main register):
```
# VALD Client Register
# Format:
# #$ Full Name
# email@domain.com

#$ John Doe
john.doe@university.edu

#$ Jane Smith
jane.smith@research.org
```

**`config/clients.register.local`** (local register):
```
# Local VALD Client Register

#$ Local Test User
local@localhost
```

### Site Configuration

Edit `vald_web/settings.py` to configure site-specific settings:

```python
# VALD-specific configuration
SITENAME = 'VALD'                    # Site name shown in header
MAINTENANCE = False                   # Set to True to show maintenance page
```

### Directory Structure

The application expects the following directory structure:

```
vald-www/
├── config/
│   ├── clients.register          # Main user register
│   ├── clients.register.local    # Local user register
│   ├── default.cfg               # Default linelist config
│   ├── htmldefault.cfg           # Default HTML preferences
│   └── personal_configs/         # User-specific configs (auto-created)
├── documentation/                # Documentation HTML files
├── news/                         # News items
├── requests/                     # Email templates for requests
│   ├── contact-req.txt
│   ├── extractall-req.txt
│   ├── extractelement-req.txt
│   ├── extractstellar-req.txt
│   └── showline-req.txt
├── style/
│   └── style.css                 # Original CSS (auto-included)
├── vald/                         # Django app
├── vald_web/                     # Django project
├── manage.py
└── pyproject.toml
```

## Testing

### Test Users

Two test users are pre-configured in `config/clients.register`:

- `test@example.com` (Test User)
- `john.doe@university.edu` (John Doe)

### Test Login

1. Start the server: `uv run manage.py runserver`
2. Navigate to http://127.0.0.1:8000/
3. Enter a test email (e.g., `test@example.com`)
4. Click Login
5. You should now see the logged-in interface with form buttons

### Test Email Sending

For testing without a real SMTP server, you can use Django's console email backend:

In `vald_web/settings.py`:
```python
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

This will print emails to the console instead of sending them.

## Differences from PHP Version

### Implemented Features

- ✅ All 5 main forms (Extract All, Extract Element, Extract Stellar, Show Line, Contact)
- ✅ File-based email authentication
- ✅ User preferences (units, medium, wavelength)
- ✅ Email submission of requests
- ✅ Documentation pages
- ✅ News items
- ✅ Session management
- ✅ Original HTML + CSS layout
- ✅ Form validation (JavaScript + server-side)

### Not Yet Implemented

- ⏳ Personal Configuration (linelist editing) - placeholder exists
- ⏳ Show Line Online (direct execution) - placeholder exists
- ⏳ SVN version display

### Key Changes

- **Database:** SQLite instead of file-based personal configs for user preferences
- **Sessions:** Django sessions instead of PHP sessions
- **Email:** Django email backend (SMTP with STARTTLS) instead of PHP mail()
- **Configuration:** Python settings.py instead of PHP config files

## Development

### Adding New Users

Edit `config/clients.register` and add:
```
#$ Full Name
email@domain.com
```

### Modifying Forms

Form templates are in `vald/templates/vald/`:
- `extractall.html`
- `extractelement.html`
- `extractstellar.html`
- `showline.html`
- `contact.html`

### Modifying Email Templates

Request templates are in `requests/`:
- `contact-req.txt`
- `extractall-req.txt`
- `extractelement-req.txt`
- `extractstellar-req.txt`
- `showline-req.txt`

Use `$variable` syntax for template variables (same as PHP version).

## Troubleshooting

### Email Not Sending

1. Check SMTP settings in `vald_web/settings.py`
2. Verify credentials are correct
3. Check firewall allows outbound connections on port 587
4. Use console backend for testing: `EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'`

### Login Failed

1. Check that email is in `config/clients.register` or `config/clients.register.local`
2. Ensure email format is: lowercase, one per line, with `#$ Name` comment above
3. Check file permissions on register files

### Static Files Not Loading

If CSS isn't loading, run:
```bash
python manage.py collectstatic
```

## License

Same as original VALD project.

## Support

For issues related to this Django implementation, please check the codebase or contact the maintainer.

For VALD database questions, visit http://vald.astro.uu.se/
