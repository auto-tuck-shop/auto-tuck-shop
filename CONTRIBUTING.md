# Contributing to Auto Tuck Shop

Thanks for contributing! This doc explains how to make changes to the codebase.

## 🚨 Branch Protection Rules

This repository has branch protection enabled:
- **Direct pushes to `main` are blocked**
- **All changes require a Pull Request**
- **PRs require approval before merging**

## 📝 Making Changes

### 1. Create a feature branch

Always branch off from `main`:

```powershell
git checkout main
git pull origin main
git checkout -b your-feature-name
```

**Branch naming tips:**
- Use descriptive names: `fix-voice-parsing`, `add-inventory-report`, `update-deployment-docs`
- Use hyphens, not underscores or spaces
- Keep it short but clear

### 2. Make your changes

Edit code, add files, whatever you need to do. Test locally before committing!

### 3. Commit your changes

```powershell
git add .
git commit -m "Brief description of what you changed"
```

**Good commit messages:**
- ✅ "Fix WhatsApp webhook signature verification"
- ✅ "Add currency support for ZWL"
- ✅ "Update onboarding docs with new WhatsApp setup steps"
- ❌ "Fixed stuff"
- ❌ "Update"
- ❌ "asdfasdf"

### 4. Push your branch

```powershell
git push origin your-feature-name
```

If this is the first push, Git will show you a link to create a PR - super convenient!

### 5. Create a Pull Request

Go to: https://github.com/dev-thandabantu/auto-tuck-shop/pulls

Click **"New pull request"** or use the link that appeared after pushing.

**In your PR description, include:**
- What you changed and why
- How to test it
- Any relevant context (screenshots, error messages, etc.)

**Example PR description:**
```
## What changed
Added support for Zimbabwean Dollar (ZWL) currency conversion

## Why
Shop owners in Zimbabwe use ZWL but our system only supported USD

## How to test
1. Deploy to staging
2. Set a user's currency to ZWL in admin
3. Send a test sale - should format prices correctly

## Notes
Exchange rate is hardcoded for now, needs API integration later
```

### 6. Wait for review

Someone will review your code. They might:
- ✅ Approve it immediately
- 💬 Ask questions or request changes
- ❌ Reject it (rare, but possible if it breaks something)

Don't take feedback personally - it's about making the code better, not about you!

### 7. Address feedback (if any)

If changes are requested:

```powershell
# Make the changes
git add .
git commit -m "Address review feedback"
git push origin your-feature-name
```

The PR will automatically update!

### 8. Merge!

Once approved, click **"Merge pull request"** on GitHub.

Then clean up your local branches:
```powershell
git checkout main
git pull origin main
git branch -d your-feature-name  # Delete local branch
```

## 🧪 Before Creating a PR

**Always:**
1. ✅ Test your changes locally
2. ✅ Check for obvious errors (typos, broken imports)
3. ✅ Make sure the code follows existing patterns
4. ✅ Update documentation if you changed behavior

**For code changes:**
1. ✅ Run tests: `python -m pytest tests/ -x`
2. ✅ Check for Python errors: `python manage.py check`

**For deployment changes:**
1. ✅ Deploy to staging first: `fly deploy -c fly.staging.toml`
2. ✅ Test manually in staging
3. ✅ Run automated tests against staging

## 🚫 What NOT to Do

- ❌ Push directly to main (impossible anyway due to branch protection)
- ❌ Commit secrets (API keys, passwords) - use environment variables!
- ❌ Edit migration files that are already committed
- ❌ Make huge PRs that change 50 files - break them up!
- ❌ Merge without approval

## 🆘 Need Help?

- Check existing docs: README.md, ONBOARDING.md, CLAUDE.md
- Look at previous PRs to see examples
- Ask questions in your PR description
- Google error messages
- Check Django/Python docs

## 🎯 Good First Issues

New to the codebase? Try these:
- Documentation improvements
- Adding comments to confusing code
- Fixing typos
- Writing tests for existing features
- Small bug fixes

---

Remember: Everyone was new once. Making mistakes is part of learning. The PR workflow is here to catch mistakes before they reach production, so experiment and learn! 🚀
