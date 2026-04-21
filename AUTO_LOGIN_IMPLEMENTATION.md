# Auto-Login Implementation (Session Persistence)

## Overview
Users who check "Rester connecté" can now be automatically logged in on subsequent visits without entering their password. The system uses secure, server-generated tokens stored in localStorage.

## Architecture

### 1. Database Model (`accounts/models.py`)
**`PersistentAuthToken`** — Stores persistent authentication tokens
- `user`: OneToOne relationship with Django User
- `token`: Unique hexadecimal token (64 chars)
- `created_at`: Timestamp
- `expires_at`: Token expiration (30 days by default)
- `is_valid()`: Method to check if token is not expired

### 2. API Endpoints (`accounts/views.py`)

#### `POST /api/auth/token/get/`
**Purpose**: Retrieve the token after successful login
- Only callable by authenticated users
- Returns: `{ token, expires_at }`
- Called by JavaScript after login form submission

#### `POST /api/auth/token/verify/`
**Purpose**: Verify token and auto-authenticate user
- Accepts FormData with `token` parameter
- Validates token existence and expiration
- If valid: Logs in user and returns redirect URL
- If invalid/expired: Returns 401 error
- Called by JavaScript on page load to enable auto-login

### 3. Authentication Flow

#### Login Flow with "Rester connecté" checked:
1. User submits login form with credentials + checkbox state
2. `login_view` processes credentials normally
3. If credentials valid + "remember" flag set:
   - Creates `PersistentAuthToken` for the user
   - Django session established normally
4. User redirected to dashboard
5. JavaScript calls `/api/auth/token/get/` to retrieve token
6. Token + expiration stored in localStorage

#### Auto-Login Flow (Subsequent Visits):
1. User visits site and page loads
2. Global script in `base.html` runs `_attemptAutoLoginWithToken()`
3. Check if token exists in localStorage
4. If token exists + not expired:
   - Call `/api/auth/token/verify/` with token
   - Backend verifies and authenticates user
   - User automatically redirected to dashboard
5. Transparent to user — appears as if they were auto-logged in

#### Logout Flow:
1. User clicks logout
2. `logout_view` deletes the `PersistentAuthToken`
3. Django session destroyed
4. User redirected to landing page

### 4. Frontend Implementation

#### Login Form (`templates/accounts/login.html`)
- "Rester connecté" checkbox
- Hidden field sends checkbox state to backend
- On form submit: Save identifier/mode to localStorage if checked
- JavaScript calls `_storeTokenAfterLogin()` after login success
- On page load: Restore saved identifier if "remember" flag set

#### Global Auto-Login (`templates/base.html`)
- Meta tag `user-authenticated` indicates if user is logged in
- Script `_attemptAutoLoginWithToken()` runs at page load
- Retrieves token from localStorage
- Validates expiration date
- Calls `/api/auth/token/verify/` if token valid
- Handles failed verification by cleaning up localStorage

### 5. Storage

#### localStorage Keys:
- `ou_tou_bon_identifier`: Email or phone number (for pre-filling form)
- `ou_tou_bon_mode`: 'email' or 'phone'
- `ou_tou_bon_remember`: 'true' if user checked "Rester connecté"
- `ou_tou_bon_auth_token`: Auth token (Only if "remember" checked)
- `ou_tou_bon_token_exp`: Token expiration timestamp

#### Sensitive Data:
- **Password**: NEVER stored (security best practice)
- Only random UUID token stored (generated server-side)

## Security Considerations

✅ **Secure Design**:
- Passwords never stored client-side
- Tokens generated server-side (random, unique)
- Token expiration enforced (30 days)
- Token can be revoked independently
- Session-based backend for additional protection

⚠️ **XSS Protection**:
- localStorage vulnerable to XSS attacks
- Mitigation: Use CSP headers, sanitize inputs
- Token-based approach is inherently safer than password storage

## URLs

| Route | Method | Purpose |
|-------|--------|---------|
| `/login/` | GET/POST | Main login form |
| `/logout/` | GET | Logout & delete token |
| `/api/auth/token/get/` | POST | Retrieve token after login |
| `/api/auth/token/verify/` | POST | Verify & auto-login with token |

## Database Schema

```sql
-- accounts_persistentauthtoken
CREATE TABLE accounts_persistentauthtoken (
  id INTEGER PRIMARY KEY,
  user_id INTEGER UNIQUE,
  token VARCHAR(64) UNIQUE,
  created_at DATETIME,
  expires_at DATETIME,
  FOREIGN KEY(user_id) REFERENCES auth_user(id)
);
```

## Testing Checklist

- [ ] Install with `.../manage.py migrate accounts`
- [ ] Login form displays "Rester connecté" checkbox
- [ ] Token created in database when checkbox is checked
- [ ] Token stored in localStorage after login
- [ ] New browser/tab auto-logs in with saved token
- [ ] Logout deletes token from database & localStorage
- [ ] Expired token (>30 days) is rejected
- [ ] Invalid token triggers cleanup in localStorage
- [ ] User not logged in if only localStorage exists (DB token deleted)
- [ ] Mobile responsive auto-login works

## Files Modified

- `accounts/models.py`: Added `PersistentAuthToken` model
- `accounts/views.py`: Added token generation/verification logic
- `accounts/urls.py`: Added API endpoint routes
- `templates/accounts/login.html`: Added auto-login UI & localStorage handling
- `templates/base.html`: Added global auto-login script

## Migration

```bash
python manage.py makemigrations accounts
python manage.py migrate accounts
```

## Troubleshooting

**Users not auto-logging in:**
1. Check browser console for JS errors
2. Verify token in localStorage: `localStorage.getItem('ou_tou_bon_auth_token')`
3. Check Django logs for `/api/auth/token/verify/` errors
4. Ensure user checked "Rester connecté" during login

**Token not storing:**
1. Check `/api/auth/token/get/` response in Network tab
2. Verify user is authenticated after form submit
3. Check localStorage quota not exceeded

**XSS concerns:**
- Enable Content Security Policy (CSP) headers
- Regularly audit input sanitization
- Consider using HttpOnly cookies for tokens (requires backend session)

## Future Enhancements

- [ ] Implement token refresh (extend expiration on activity)
- [ ] Add device fingerprinting for additional security
- [ ] Implement "Remember device" vs "Remember browser"
- [ ] Add admin dashboard to revoke user tokens
- [ ] Email notification on auto-login from new device
- [ ] Rate limiting on token verification attempts
