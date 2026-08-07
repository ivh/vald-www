# Auth Migration Plan: Email-Only → Password Auth

## Current State
- Users login with email only (validated against `clients.register` files)
- No passwords required
- Session-based authentication

## Migration Goal
- Force password authentication
- Require email activation link to set password
- No grace period - block login until activated

## Migration Steps

### 1. Preparation
- Add `is_activated` boolean field to User model
- Create Django User accounts for all emails in register files
  - Set `is_activated=False`
  - Set unusable password initially
- Generate unique activation tokens for each user

### 2. Send Activation Emails
- Management command to send activation emails to all users
- Email contains:
  - Unique activation link: `/activate/<user_id>/<token>/`
  - Explanation of change
  - Link expires in 48 hours

### 3. Login Flow Change
- User enters email at login
- Check if Django User exists with that email:
  - **If NO**: Fall back to file-based validation (for new users not yet in DB)
  - **If YES and is_activated=True**: Show password field, authenticate normally
  - **If YES and is_activated=False**: Block login, show message:
    - "Please activate your account via the email we sent"
    - Option to resend activation email

### 4. Activation Flow
- User clicks `/activate/<user_id>/<token>/`
- Verify token is valid and not expired
- Show password creation form
- User sets password
- Set `is_activated=True`
- Redirect to login
- User can now login with email + password

### 5. Ongoing User Management
- New users added via admin interface or management command
- Send activation email automatically
- Can manually reset/resend activation links via admin

## Security
- Activation tokens are single-use, time-limited (48h)
- Email link proves email ownership
- Can't hijack account by just knowing email
- CSRF protection on all forms

## Implementation Files
- `models.py`: Add is_activated field (optional - can use User.has_usable_password())
- `management/commands/migrate_users.py`: Create Users from register files
- `management/commands/send_activations.py`: Send activation emails
- `views.py`: Update login view, add activate view
- `templates/`: activation email, password set form, blocked login page
- `urls.py`: Add activation URL pattern

## Rollback Plan
- Keep register files in place
- Can switch back to file-based auth by reverting login view
- No data loss
