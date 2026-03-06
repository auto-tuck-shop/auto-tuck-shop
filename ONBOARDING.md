# 🚀 Auto Tuck Shop - Onboarding Guide

Welcome! This doc will help you understand what this project is, what it does, and how to get everything set up and running.

## 🤔 What Is This Thing?

**Auto Tuck Shop** is a WhatsApp bot that helps small shop owners (tuckshops) track their sales via voice or text messages. 

**How it works:**
1. Shop owner sends a WhatsApp message like "I sold 2 cokes for $1 each and 3 breads for $0.50"
2. The bot uses AI to parse what was sold
3. It stores the sale in a database and sends a confirmation
4. Owners can track inventory, sales history, and revenue

**The tech:**
- **Django** (Python web framework) - handles all the logic
- **WhatsApp Business API** (Meta) - receives/sends messages
- **OpenRouter** (AI service) - parses messages and handles conversations
- **ElevenLabs** - transcribes voice messages to text
- **Cloudflare R2** - stores voice/media files
- **Fly.io** - hosts the app
- **PostgreSQL** - database
- **Sentry** (optional) - error tracking

---

## 🎯 Quick Start Checklist

Before you can run pilots in Zimbabwe, you need to:

- [ ] Set up a WhatsApp Business account (fresh start after the ban)
- [ ] Create accounts for all services
- [ ] Set up local development environment
- [ ] Deploy to staging and test
- [ ] Deploy to production

**Estimated time:** 4-8 hours total (varies based on approval times for WhatsApp Business)

---

## 📋 Part 1: Service Accounts You Need

### 1. Meta WhatsApp Business Platform ⚠️ CRITICAL

**Why:** This is how the bot sends/receives messages. Without it, nothing works.

**What happened:** The previous WhatsApp account got restricted/banned by Meta.

**What you need:**
- A new Meta Business account
- WhatsApp Business Platform app
- A phone number to use as the WhatsApp Business number (can't be already on WhatsApp)

**Steps:**
1. Go to [Meta for Developers](https://developers.facebook.com/)
2. Create a new developer account (use your own email/phone)
3. Create a new Meta Business App
4. Add WhatsApp product to your app
5. Follow Meta's verification process (might take days/weeks - they're strict)
6. Get your:
   - `Access Token` 
   - `Phone Number ID`
   - `App Secret`
   - Verify Token (you create this yourself - just a random string)

**Resources:**
- [WhatsApp Business Platform Docs](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started)
- [Phone number requirements](https://developers.facebook.com/docs/whatsapp/phone-numbers)

**Cost:** Free for first 1,000 conversations/month, then pay-as-you-go

---

### 2. OpenRouter 🤖

**Why:** This is the AI that understands what users are saying and responds intelligently.

**Steps:**
1. Go to [OpenRouter.ai](https://openrouter.ai/)
2. Sign up with your email
3. Add credits ($5-10 should be plenty for testing)
4. Get your API key

**Model used:** `google/gemini-2.5-flash-lite` (cheap and fast)

**Cost:** ~$0.01 per 1000 messages (very cheap)

---

### 3. ElevenLabs 🎤

**Why:** Converts voice messages to text so the AI can understand them.

**Steps:**
1. Go to [ElevenLabs.io](https://elevenlabs.io/)
2. Sign up (they have a free tier)
3. Get your API key from settings

**Cost:** Free tier gives 10,000 characters/month (enough for testing)

---

### 4. Cloudflare R2 ☁️

**Why:** Stores voice messages and media files (like AWS S3 but cheaper).

**Steps:**
1. Go to [Cloudflare](https://cloudflare.com) and create account
2. Go to R2 section
3. Create a bucket called `auto-tuck-shop-production`
4. Create another bucket called `auto-tuck-shop-staging`
5. Get your:
   - Access Key ID
   - Secret Access Key
   - Endpoint URL (format: `https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com`)
   - Set up a public URL for your bucket (or use R2's default public URL)

**Cost:** First 10GB free, then super cheap ($0.015/GB)

---

### 5. Fly.io 🚁

**Why:** This is where your app runs (hosting platform).

**Steps:**
1. Go to [Fly.io](https://fly.io) and sign up
2. Install flyctl CLI: `powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"`
3. Run `fly auth login`
4. You'll need to:
   - Create a Postgres database: `fly postgres create` (choose name like `auto-tuck-shop-db`)
   - Attach it to your apps later

**Cost:** Free tier includes 3 small VMs and 3GB Postgres (enough to start)

---

### 6. Sentry 🐛 (Optional)

**Why:** Tracks errors in production so you know when things break.

**Steps:**
1. Go to [Sentry.io](https://sentry.io)
2. Create a new Django project
3. Get your DSN (a URL that looks like `https://...@sentry.io/...`)

**Cost:** Free tier is generous (5k errors/month)

---

## 💻 Part 2: Local Development Setup

### Prerequisites
- Python 3.11+ installed
- Git installed
- VS Code or any code editor
- Windows PowerShell (you're already here!)

### Steps

**1. Clone the repo** (already done!)
```powershell
cd C:\Users\User\source\repos\auto-tuck-shop
```

**2. Create a virtual environment**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**3. Install dependencies**
```powershell
pip install -r requirements.txt
pip install -r requirements-dev.txt  # for testing
```

**4. Set up your `.env` file**

Copy the example:
```powershell
cp .env.example .env
```

Edit `.env` with your actual values:
```env
SECRET_KEY=make-up-a-random-long-string-here
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database (for local dev, use SQLite)
DATABASE_URL=sqlite:///db.sqlite3

# Meta WhatsApp (get from Part 1)
META_WHATSAPP_ACCESS_TOKEN=your-token
META_WHATSAPP_PHONE_NUMBER_ID=your-phone-id
META_WHATSAPP_VERIFY_TOKEN=make-up-any-string
META_WHATSAPP_APP_SECRET=your-app-secret

# OpenRouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=google/gemini-2.5-flash-lite

# ElevenLabs
ELEVENLABS_API_KEY=your-key

# Cloudflare R2
R2_ACCESS_KEY_ID=your-key
R2_SECRET_ACCESS_KEY=your-secret
R2_ENDPOINT_URL=https://your-account.r2.cloudflarestorage.com
R2_BUCKET_NAME=auto-tuck-shop-production
R2_PUBLIC_URL=https://your-public-url

# Sentry (optional)
SENTRY_DSN=your-sentry-dsn
```

**5. Run migrations**
```powershell
python manage.py migrate
```

**6. Create a superuser (for admin access)**
```powershell
python manage.py createsuperuser
```

**7. Start the dev server**
```powershell
python manage.py runserver
```

Visit `http://localhost:8000/admin` to see the admin panel!

---

## 🌐 Part 3: Deploy to Staging

Staging is a test environment where you can experiment without breaking production.

**1. Create staging app on Fly.io**
```powershell
fly apps create auto-tuck-shop-staging
```

**2. Create a staging database**
```powershell
fly postgres create --name auto-tuck-shop-db-staging
fly postgres attach auto-tuck-shop-db-staging --app auto-tuck-shop-staging
```

**3. Set staging secrets**

You need to set all your environment variables as Fly secrets:

```powershell
fly secrets set --app auto-tuck-shop-staging `
  SECRET_KEY="your-secret-key" `
  META_WHATSAPP_ACCESS_TOKEN="your-token" `
  META_WHATSAPP_PHONE_NUMBER_ID="your-phone-id" `
  META_WHATSAPP_VERIFY_TOKEN="your-verify-token" `
  META_WHATSAPP_APP_SECRET="your-app-secret" `
  OPENROUTER_API_KEY="your-key" `
  ELEVENLABS_API_KEY="your-key" `
  R2_ACCESS_KEY_ID="your-key" `
  R2_SECRET_ACCESS_KEY="your-secret" `
  R2_ENDPOINT_URL="your-endpoint" `
  R2_PUBLIC_URL="your-public-url" `
  ALLOWED_HOSTS="auto-tuck-shop-staging.fly.dev" `
  CSRF_TRUSTED_ORIGINS="https://auto-tuck-shop-staging.fly.dev"
```

Note: `R2_BUCKET_NAME` is set to `auto-tuck-shop-staging` in `fly.staging.toml` already.

**4. Deploy!**
```powershell
fly deploy -c fly.staging.toml
```

**5. Test it**

Staging uses a **mock WhatsApp client** so you don't need real WhatsApp to test.

1. Go to `https://auto-tuck-shop-staging.fly.dev/mock-whatsapp/`
2. Log in with your Django admin credentials
3. You can now send test messages and see responses!

Run automated tests:
```powershell
# First, create .env.staging file
echo "STAGING_URL=https://auto-tuck-shop-staging.fly.dev" > .env.staging
echo "TEST_API_KEY=your-test-key" >> .env.staging
echo "META_WHATSAPP_APP_SECRET=your-app-secret" >> .env.staging

python -m pytest tests/ -x
```

---

## 🚀 Part 4: Deploy to Production

**⚠️ Only do this after testing thoroughly in staging!**

**1. Create production app**
```powershell
fly apps create auto-tuck-shop
```

**2. Create production database**
```powershell
fly postgres create --name auto-tuck-shop-db
fly postgres attach auto-tuck-shop-db --app auto-tuck-shop
```

**3. Set production secrets**
```powershell
fly secrets set --app auto-tuck-shop `
  SECRET_KEY="different-secret-key" `
  META_WHATSAPP_ACCESS_TOKEN="your-token" `
  META_WHATSAPP_PHONE_NUMBER_ID="your-phone-id" `
  META_WHATSAPP_VERIFY_TOKEN="your-verify-token" `
  META_WHATSAPP_APP_SECRET="your-app-secret" `
  OPENROUTER_API_KEY="your-key" `
  ELEVENLABS_API_KEY="your-key" `
  R2_ACCESS_KEY_ID="your-key" `
  R2_SECRET_ACCESS_KEY="your-secret" `
  R2_ENDPOINT_URL="your-endpoint" `
  R2_BUCKET_NAME="auto-tuck-shop-production" `
  R2_PUBLIC_URL="your-public-url" `
  ALLOWED_HOSTS="auto-tuck-shop.fly.dev" `
  CSRF_TRUSTED_ORIGINS="https://auto-tuck-shop.fly.dev"
```

**4. Deploy**
```powershell
fly deploy
```

**5. Set up WhatsApp webhook**

1. Go to your Meta app dashboard
2. WhatsApp → Configuration
3. Set webhook URL: `https://auto-tuck-shop.fly.dev/whatsapp/webhook/`
4. Set verify token: (whatever you used for `META_WHATSAPP_VERIFY_TOKEN`)
5. Subscribe to message events

Now you're live! 🎉

---

## 📊 Part 5: How to Use the Admin Panel

**Access:** `https://auto-tuck-shop.fly.dev/admin` (or staging URL)

**Key sections:**

1. **Core → User Profiles**
   - These are your shop owners
   - Each shop owner needs to be approved from the waitlist first

2. **Core → Waitlist Entries**
   - New users who sent a message but aren't approved yet
   - **You need to manually approve them** to give them access
   - Once approved, they become a User Profile

3. **Sales → Sales**
   - See all sales recorded
   - Can mark sales as "bot mistake" if AI parsed wrong

4. **Catalog → Products**
   - Products that shop owners sell
   - The AI learns from this catalog

5. **WhatsApp → WhatsApp Messages**
   - Message history (what users sent, what bot replied)

---

## 🔧 Common Tasks

### Update the admin phone number

The admin phone gets notified when someone new joins the waitlist. Currently hardcoded to Jonah's number.

Edit [apps/whatsapp/services/webhook_handler.py](apps/whatsapp/services/webhook_handler.py#L50):
```python
ADMIN_PHONE_NUMBER = "+263..." # your new admin number
```

### Add a new shop owner manually

1. Go to admin panel
2. Core → User Profiles → Add
3. Fill in phone number, name, company name
4. Save
5. They can now use the bot!

### View logs and errors

**Staging/Production logs:**
```powershell
fly logs --app auto-tuck-shop
fly logs --app auto-tuck-shop-staging
```

**Sentry (if set up):**
Go to your Sentry dashboard to see errors with full stack traces.

### Run Django commands on production

```powershell
fly ssh console --app auto-tuck-shop
> python manage.py shell
```

---

## 🧪 Understanding the Tests

The `tests/` folder has automated tests that simulate real user workflows:

- `test_onboarding_workflow.py` - New user joins waitlist
- `test_text_sale_workflow.py` - User records sale via text
- `test_audio_sale_workflow.py` - User records sale via voice
- `test_confirmation_workflow.py` - User confirms or edits sale
- `test_localizations.py` - Tests English and Shona translations

**Run all tests:**
```powershell
python -m pytest tests/ -v
```

**Run one test:**
```powershell
python -m pytest tests/test_onboarding_workflow.py -v
```

Tests hit the **staging environment** and use the mock WhatsApp client.

---

## 🐛 Troubleshooting

### "WhatsApp message not received"
- Check your webhook URL is correct in Meta dashboard
- Check Fly logs: `fly logs --app auto-tuck-shop`
- Verify `META_WHATSAPP_VERIFY_TOKEN` matches what's in Meta

### "AI parsing is wrong"
- Check OpenRouter API key is set correctly
- Look at prompts in `services/openrouter/prompts.py`
- You can tweak the AI prompts to improve accuracy

### "Voice messages not working"
- Check ElevenLabs API key and quota
- Check R2 bucket is public and accessible
- Verify R2 secrets are set correctly

### "Database migration errors"
- Never edit old migration files!
- Always create new migrations: `python manage.py makemigrations`
- Apply them: `python manage.py migrate`

### "Fly deployment fails"
- Check your Fly.io quotas (free tier limits)
- Check all secrets are set: `fly secrets list --app auto-tuck-shop`
- Check logs: `fly logs --app auto-tuck-shop`

---

## 📚 Important Files to Know

- **`manage.py`** - Django command-line tool
- **`config/settings/`** - Settings for development, staging, production
- **`apps/whatsapp/services/webhook_handler.py`** - Main message handling logic
- **`apps/whatsapp/services/message_parser.py`** - AI parsing logic
- **`apps/sales/models.py`** - Database models for sales
- **`services/openrouter/prompts.py`** - AI prompts (tune these!)
- **`fly.toml`** and **`fly.staging.toml`** - Fly.io config
- **`requirements.txt`** - Python dependencies
- **`tests/`** - Automated tests

---

## 🎓 Learning Resources

**Django:**
- [Django Docs](https://docs.djangoproject.com/)
- [Django Tutorial](https://docs.djangoproject.com/en/stable/intro/tutorial01/)

**WhatsApp Business API:**
- [Cloud API Docs](https://developers.facebook.com/docs/whatsapp/cloud-api)

**Git:**
- [Git Handbook](https://guides.github.com/introduction/git-handbook/)

**Python:**
- [Real Python](https://realpython.com/)

---

## 💡 Next Steps

Once everything is deployed and working:

1. **Test with real users** - Get some tuckshop owners in Zimbabwe to try it
2. **Monitor errors** - Check Sentry and logs daily
3. **Iterate on prompts** - Tune the AI based on real usage
4. **Add features** - Inventory tracking, sales reports, etc.
5. **Scale** - If it works, upgrade Fly.io plan for more capacity

---

## ⚠️ Important Notes

1. **Never commit secrets** - Always use environment variables, never hardcode API keys
2. **Test in staging first** - Always deploy to staging, run tests, then deploy to prod
3. **Migrations are immutable** - Never edit old migration files (see CLAUDE.md)
4. **WhatsApp is strict** - Follow their policies or you'll get banned again
5. **Costs add up** - Monitor your usage on all platforms

---

## 🆘 Getting Help

**Stuck?** Here's what to do:

1. Check the error message carefully
2. Search the error in Google or Stack Overflow
3. Check the relevant service docs (Django, Fly.io, etc.)
4. Look at the code comments
5. Check GitHub issues on similar projects
6. Ask in Django Discord or Python communities

**Remember:** Everyone was a beginner once. It's okay to not know stuff. Google is your friend, docs are your friend, and trial-and-error is how you learn. You got this! 💪

---

## 📝 Project Status

**Current state:** 
- Code is stable and tested
- Previous WhatsApp account was banned/restricted
- Need fresh WhatsApp Business account to resume

**What works:**
- Text and voice message parsing
- Sale recording and confirmation
- Multi-language support (English, Shona)
- Waitlist system
- Admin dashboard
- Staging environment with mock WhatsApp
- Automated test suite

**What needs to be done:**
- Set up new WhatsApp Business account ⚠️ BLOCKER
- Deploy to new Fly.io account under your name
- Update admin phone number in code
- Test with real pilot shop owners in Zimbabwe

Good luck! 🍀
