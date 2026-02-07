# Auto Tuckshop

WhatsApp-based sales tracking system for tuck shops in Zimbabwe. Record sales via text or voice, get daily summaries, and track inventory - all through WhatsApp.

## Quick Start (Local Testing)

### 1. Set Up Environment

```powershell
# Activate virtual environment
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the root directory (or copy from `.env.example`):

```env
SECRET_KEY=your-secret-key-here
DATABASE_URL=sqlite:///db.sqlite3
DEBUG=True

# WhatsApp API
WHATSAPP_TOKEN=your-whatsapp-token
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
VERIFY_TOKEN=your-verify-token

# OpenRouter (for LLM parsing)
OPENROUTER_API_KEY=your-openrouter-key

# ElevenLabs (for voice transcription)
ELEVENLABS_API_KEY=your-elevenlabs-key

# Cloudflare R2 (for media storage)
R2_ENDPOINT=your-r2-endpoint
R2_ACCESS_KEY_ID=your-access-key
R2_SECRET_ACCESS_KEY=your-secret-key
R2_BUCKET_NAME=your-bucket-name
R2_PUBLIC_URL=your-public-url
```

### 3. Run Migrations

```powershell
python manage.py migrate
```

### 4. Create Admin Account

```powershell
python manage.py createsuperuser
```

### 5. Start Development Server

```powershell
python manage.py runserver
```

Access the admin portal at: `http://localhost:8000/admin`

### 6. Test Without Deploying

#### Option A: Using ngrok (Recommended for WhatsApp Testing)

1. Install ngrok: https://ngrok.com/download
2. Start your Django server: `python manage.py runserver`
3. In a new terminal, run ngrok:
   ```powershell
   ngrok http 8000
   ```
4. Copy the HTTPS URL (e.g., `https://abc123.ngrok.io`)
5. Update your WhatsApp webhook in Meta Business Suite:
   - Webhook URL: `https://abc123.ngrok.io/webhook/whatsapp/`
   - Verify Token: (from your `.env` file)
   
Now you can test with real WhatsApp messages!

#### Option B: Local Testing with Mock Data

Use the mock server in the `mock/` directory to simulate WhatsApp conversations:

```powershell
cd mock
npm install
npm start
```

Open http://localhost:3000 to test the conversation flow UI.

## Testing the WhatsApp Bot

### First-Time User Flow

1. **Waitlist**: Send any message to the bot
   - Bot: "You're on the waitlist. Admin will approve you soon."
   
2. **Admin Approval**: Login to admin portal and approve the user

3. **Language Selection**:
   - Bot: "1️⃣ English\n2️⃣ Shona"
   - User: "1" or "2"

4. **Role Selection**:
   - Bot: "Are you the owner or assistant?"
   - User: "1" (Owner), "2" (Assistant), or "3" (Both)

5. **Assistant Linking** (if role is Assistant):
   - Bot: "What's your name?"
   - User: "John"

6. **Stock Setup** (optional):
   - Bot: "Want to add products now?"
   - User: "yes" → Add products, or "no" → Skip

7. **Ready to Log Sales**:
   - Bot: "Start recording sales!"

### Logging Sales

**Text messages:**
- "2 cokes, 1 chips"
- "sold 3 maputi at $1 each"
- "1 coke $2"

**Voice messages:**
- Record: "I sold 2 cokes and 1 chips"

**Confirmation:**
- Bot shows summary → User replies "yes" or "✓" to confirm

### Getting Summaries

| Command | Description |
|---------|-------------|
| "done" or "done for today" | Daily summary (doesn't close day) |
| "week" or "weekly" | This week's sales (Monday-Sunday) |
| "month" or "monthly" | This month's sales |
| "help" | Show available commands |

After receiving a summary, reply "yes" or "1" to enable automatic daily summaries.

### Testing Keywords

- **Sales**: "sold 2 cokes", "2x chips $1", "mafuta $5"
- **Confirmation**: "yes", "confirm", "✓", "ok"
- **Summaries**: "done", "week", "monthly", "help"
- **Language switch**: "change language" (not yet implemented)

## Development

### Project Structure

```
apps/
├── catalog/       # Products, categories, pricing
├── core/          # User profiles, companies, waitlist
├── inventory/     # Stock tracking (future)
├── sales/         # Sales records, line items
└── whatsapp/      # Message handling, webhook
    ├── locales/   # English & Shona translations
    └── services/  # Message parsing, WhatsApp client
config/
└── settings/      # Base, development, production
services/
├── elevenlabs/    # Voice transcription
├── openrouter/    # LLM parsing
└── storage/       # R2 media storage
```

### Running Tests

```powershell
python manage.py test
```

### Common Issues

**"SECRET_KEY not set"**
→ Create `.env` file with SECRET_KEY

**"UnicodeDecodeError" when starting**
→ Already fixed (webhook_handler.py uses UTF-8 encoding)

**Sales not saving**
→ Check database connection in `.env`

**LLM parsing fails**
→ Check OPENROUTER_API_KEY and credits

**Voice messages fail**
→ Check ELEVENLABS_API_KEY and credits

## Database

**Development**: SQLite (`db.sqlite3`)
**Production**: PostgreSQL (configured via DATABASE_URL)

To reset the database:
```powershell
del db.sqlite3
python manage.py migrate
python manage.py createsuperuser
```

## Deployment

The app is configured for deployment on Fly.io. See `fly.toml` and `Dockerfile`.

```powershell
fly deploy
```

## Multi-Language Support

- English (en) and Shona (sn) supported
- User selects language during onboarding
- All messages localized via `apps/whatsapp/locales/`
- Product names NOT translated (stored as-is)

## Multi-Currency Support

Supported currencies: USD, ZWG, ZAR, BWP, EUR, GBP
- Currency detected per-sale from message
- Default currency set per company in admin

## Admin Portal

Access: `http://localhost:8000/admin` (or your deployed URL)

**Manage**:
- Waitlist approvals
- Users & companies
- Sales records
- Products & pricing
- WhatsApp messages (debug)

## License

Proprietary - All rights reserved
