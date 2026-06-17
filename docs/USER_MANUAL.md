# Hospital Flow User Manual

## Getting Started

### Accessing the System

1. **Streamlit Portal**: Open http://localhost:8501 in your browser
2. **API Documentation**: Open http://localhost:8000/docs (development only)

### Login Options

The portal has three access modes:

1. **Hospital Portal** — For hospital staff and patients
2. **Super Admin Portal** — For system administrators
3. **Register New Hospital** — For new hospital registration

## User Roles

### Super Admin
- Full system access
- Manage all hospitals (tenants)
- Manage subscription plans
- View audit logs
- Create/manage other super admins

### Hospital Admin
- Manage users within their hospital
- View hospital dashboard
- Cannot access other hospitals

### Hospital Staff
- **Nurse**: Patient care, triage
- **Clinician**: General consultations
- **Doctor**: Diagnosis, prescriptions
- **Lab Technician**: Lab tests
- **Radiographer**: Imaging
- **Pharmacist**: Medication dispensing
- **Cashier**: Billing and payments

### Patient
- View own records
- Book appointments
- View prescriptions

## Hospital Portal

### Dashboard
After login, the dashboard shows:
- Hospital information
- Active user count
- Subscription status
- Quick links

### User Management (Hospital Admin only)

#### Adding a User
1. Navigate to **Users** tab
2. Click **Add User**
3. Fill in:
   - Username (3-255 chars, alphanumeric, underscore, hyphen, dot)
   - Full Name
   - Email
   - Password (min 8 chars, uppercase, lowercase, digit, special char)
   - Role (nurse, clinician, doctor, patient, etc.)
4. Click **Create**

#### Editing a User
1. Click **Edit** next to the user
2. Modify fields
3. Click **Save**

#### Suspending/Resuming a User
1. Click **Edit** next to the user
2. Toggle **Is Active** checkbox
3. Click **Save**

### Subscription (Hospital Admin only)
1. Navigate to **Subscription** tab
2. View:
   - Current plan
   - Status (active, trial, suspended)
   - Billing cycle
   - Start/end dates
   - Feature gates
   - Suspension details

## Super Admin Portal

### Dashboard
- Total hospitals
- Active/suspended/terminated counts
- Subscription revenue
- Recent audit events

### Tenant Management

#### Creating a Tenant
1. Navigate to **Tenants** tab
2. Click **Create New Tenant**
3. Fill required fields:
   - Hospital Name
   - Admin Username
   - Admin Email
   - Temporary Password (min 8 chars, strong)
   - Subscription Plan (free_trial, basic, standard, premium, enterprise)
   - Billing Cycle (monthly, annual)
4. Click **Create Tenant**

The admin will be forced to change their temporary password on first login.

#### Editing a Tenant
1. Click **Edit** next to the tenant
2. Modify fields
3. Click **Save**

#### Activating a Tenant
1. Find the tenant in the list
2. Click **Activate**
3. Confirm the action

#### Suspending a Tenant
1. Find the tenant in the list
2. Click **Suspend**
3. Enter a reason (required)
4. Confirm the action

Suspension will:
- Block all user logins
- Revoke active sessions
- Add to Redis blocklist

#### Reactivating a Tenant
1. Find the suspended tenant
2. Click **Reactivate**
3. Confirm the action

#### Terminating a Tenant
1. Find the tenant in the list
2. Click **Terminate**
3. Enter a reason (required)
4. Confirm the action

**Warning**: Termination is irreversible!

### Subscription Management

#### Changing a Plan
1. Select the tenant
2. Go to **Subscription** tab
3. Select new plan
4. Click **Upgrade** or **Downgrade**

#### Starting a Free Trial
1. Select the tenant
2. Select **free_trial** plan
3. Click **Subscribe**

**Note**: Each tenant can only use the free trial once.

#### Renewing a Subscription
1. Select the tenant
2. Click **Renew**
3. Select billing cycle (optional)
4. Confirm

### Super Admin Users

#### Creating a Super Admin
1. Navigate to **Super Admins** tab
2. Click **Add Super Admin**
3. Fill in:
   - Username
   - Full Name
   - Email
   - Password (strong)
   - Role (super_admin, billing_manager, support)
4. Click **Create**

### Announcements

#### Creating an Announcement
1. Navigate to **Announcements** tab
2. Click **Create Announcement**
3. Fill in:
   - Title
   - Body
   - Audience (all or selected)
   - Target tenants (if selected)
   - Publish time
   - Expiry time (optional)
4. Click **Post**

## Password Management

### Changing Your Password
1. Click your profile name in the sidebar
2. Click **Change Password**
3. Enter current password
4. Enter new password (must meet policy)
5. Confirm new password
6. Click **Update**

### Forgot Password
1. On the login screen, click **Forgot Password**
2. Enter your email
3. Check your inbox for reset link
4. Follow the link and set a new password

### Force Password Change
If your account has **Force Password Change** enabled:
1. You will be redirected to a password change screen after login
2. Enter your temporary password
3. Enter a new strong password
4. Confirm the new password
5. Click **Update**

## Common Issues

### "Tenant subscription is suspended"
Your hospital's subscription has been suspended. Contact your system administrator.

### "Too many failed login attempts"
You have been temporarily blocked due to too many failed attempts. Wait 5 minutes and try again.

### "Invalid credentials"
Check your username and password. Passwords are case-sensitive.

### "Registration failed"
Ensure:
- Password meets strength requirements
- Username is unique
- Email is valid
- All required fields are filled

### "Internal Server Error"
This is a server-side issue. Please try again later or contact support.

## Tips

- Use the **Hospital Portal** for daily operations
- Use the **Super Admin Portal** for system management
- Always log out when finished
- Never share your password
- Use a strong, unique password
- Enable MFA if available

## Support

For technical support, contact the Hospital Flow support team.
