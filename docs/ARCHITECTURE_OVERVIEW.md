# Auto Tuck Shop - UML & System Architecture Overview

## System Summary

Auto Tuck Shop is a Django-based WhatsApp chatbot platform for recording sales in African tuck shops. It uses AI (OpenRouter/Gemini) to parse sales messages, ElevenLabs for voice transcription, and Meta's WhatsApp Business API for messaging. The system supports multi-language UI (English & Shona), multi-tenant architecture (multiple companies), and tracks sales with optional financial features (change, debt, credit).

---

## Generated UML Diagrams

### 1. **Use Case Diagram** (`uml_usecase.puml`)
**Scope:** Actor interactions with the system

**Primary Actors:**
- **Shop Owner** - Creates company, records sales, queries ledger, adds assistants
- **Shop Assistant** - Records sales, queries ledger (no management access)
- **Admin** - Approves/rejects new user applications
- **WhatsApp Bot** - Entry point for all customer interactions
- **External APIs** - OpenRouter (NLP), ElevenLabs (transcription), Meta (messaging)

**Key Use Cases:**
1. **UC1: Onboarding & Language Selection** - New users join waitlist, select language (EN/SN)
2. **UC2: Admin Approval** - Admin approves waiting users, creates Company + UserProfile
3. **UC3: Text Sale Recording** - Owner/Assistant sends text like "2 bread $1 each" → bot parses → records sale
4. **UC4: Voice Sale Recording** - Audio message → ElevenLabs transcribes → follows text flow
5. **UC5: Sale Confirmation/Fix** - User confirms parsed sale or marks it as bot mistake
6. **UC6: Record Change** - System logs customer change (overpayment) per transaction
7. **UC7: Track Debt** - System logs customer debt (underpayment) per transaction
8. **UC8: Sales Query** - User asks for sales totals/counts by date/product/customer
9. **UC9: Add Assistant** - Owner adds team member phone → creates new UserProfile
10. **UC10: WhatsApp Message Handling** - Core flow handling all inbound/outbound messages

**Workflow Dependencies:**
- UC1 → UC2 → UC3+ (Approval required before sales access)
- UC4 → UC3 (Voice routes to text parser)
- UC3 → UC5 (Sales need confirmation)
- UC3 ⟹ UC6, UC7 (May generate change/debt records)

---

### 2. **Class Diagram** (`uml_classes.puml`)
**Scope:** Data models and relationships

**Major Packages:**

#### **Organization** (Multi-tenant)
- **Company** - Shop record; primary entity for data isolation
- **UserProfile** - Owner/Assistant roles per company; linked to Django User
- **WaitlistEntry** - Pending users; converted to UserProfile on approval

#### **Catalog Management**
- **Category** - Product grouping (e.g., "Beverages", "Snacks")
- **Product** - Shop items (name, SKU, cost); company-scoped
- **ProductPrice** - Historical pricing; many per product (effective_from tracking)
- **InventoryAdjustment** - Stock changes with reason (purchase|return|damage|correction|initial)

#### **Sales Recording** (Core Feature)
- **Sale** - Transaction record; `status` (pending|confirmed|cancelled)
  - `flagged_as_bot_mistake` - User marked incorrect parse
  - `total_amount` - Sum of SaleItems (may be null in sales-only mode)
- **SaleItem** - Line items per sale; quantity, unit_price, currency
- **DraftSale** - Multi-message sale capture (user sends "2 bread" then "1 coke")
- **DraftSaleItem** - Temporary line items during draft building

#### **Financial Tracking** (Optional Features)
- **ChangeRecord** - Overpayment tracking; customer name, amount, currency
- **DebtorRecord** - Underpayment tracking; debtor name, amount, currency
- **CustomerCreditBalance** - Running balance per phone/currency; unique constraint
- **CustomerCreditTransaction** - Audit log; kind (credit_added|credit_used), amount, reason

#### **WhatsApp Integration**
- **WhatsAppMessage** - Inbound/outbound log; all message types (text|audio|button_response)
  - Relational links to Sale, UserProfile, WaitlistEntry for context
- **PendingAction** - Temporary user state during multi-step flows (e.g., "Record change" → prompt for name)

**Key Relationships:**
- All models scoped to `Company` (1-to-many) for multi-tenancy
- `Product` → `ProductPrice` (1-to-many, historical)
- `Sale` → `SaleItem` (1-to-many)
- `Sale` ← `ChangeRecord`, `DebtorRecord`, `CustomerCreditTransaction` (1-to-many)
- `DraftSale` → `DraftSaleItem` (1-to-many)

---

### 3. **Sequence Diagram - Message Processing** (`uml_sequence_message_processing.puml`)
**Scope:** Complete flow from webhook to response

**Flow Steps:**

1. **User sends message** → Meta Cloud API receives text or audio
2. **Webhook receives POST** → Django validates Meta signature
3. **Duplicate check** → If same `whatsapp_message_id` already in DB, skip
4. **Audio handling (if audio)**:
   - Download media from `media_id`
   - Call ElevenLabs transcribe_audio()
   - Extract text
5. **Message parsing**:
   - Call `parse_message_unified(text, language)`
   - OpenRouter LLM extracts intent + items
   - Rule-based hints applied (currently disabled)
6. **Intent routing**:
   - **SALE**: Create Product(s) → Create Sale → Create SaleItem(s) → Format receipt with Confirm/Fix buttons → Send via WhatsApp
   - **ADD_ASSISTANT**: Extract phone → Create UserProfile → Send confirmation
   - **QUERY**: Aggregate sales from DB by timeframe/product → Send results
   - **OTHER**: Send guidance message
7. **Outbound logging** → Record message in WhatsAppMessage table
8. **Button response (Confirm/Fix)**:
   - User clicks button → Webhook receives button_response
   - Update Sale status (confirmed or cancelled)
   - Send confirmation or "redo" message

**Error Paths** (not shown in detail):
- Audio transcription fails → Send guidance to use text
- Intent extraction fails → Send "I don't understand" + examples
- Product not found → Create new product automatically
- Database errors → Log to Sentry, send generic error to user

---

### 4. **Component Diagram** (`uml_components.puml`)
**Scope:** System architecture and layer integration

**Layers:**

#### **External APIs**
- Meta WhatsApp Cloud API (inbound messages, outbound send)
- OpenRouter (LLM for parsing)
- ElevenLabs (voice transcription)
- Cloudflare R2 (media storage)

#### **Django Application Stack**
1. **WhatsApp Integration Layer**
   - Webhook View: Validates Meta signatures, routes to handlers
   - WhatsApp Client: Sends messages via Meta API (or MockWhatsAppClient for testing)
   - Message Handler: Core business logic orchestrator

2. **NLP & Parsing**
   - Message Parser: Unified entry point for text extraction
   - Intent Classifier: Determines sale|query|add_assistant|other
   - Price/Change/Debt Extractor: Disabled in sales-only mode (kept for future re-enablement)

3. **Business Logic**
   - Sales Service: Persists sales, creates products, generates receipts
   - Draft Sale Manager: Handles multi-message basket building
   - Financial Tracker: Logs change, debt, credit transactions

4. **Models & ORM**
   - Organized by domain: Company, User, Catalog, Sales, Financial, Messaging

5. **Localization & UI**
   - Locale Manager: Loads en.json, sn.json (English, Shona)
   - Receipt Formatter: Builds sale confirmation text
   - Button Builder: Creates interactive button payloads

#### **Database**
- PostgreSQL (production/staging)
- SQLite (local development/testing)

#### **Deployment & Monitoring**
- Fly.io: Production/staging hosting
- ngrok: Local tunnel for webhook testing
- Django Admin: User/company/product management
- Sentry: Error tracking and alerting

#### **Testing**
- Mock WhatsApp Client: Simulates user interactions without real API
- Pytest: Integration tests; covers workflows end-to-end

---

## Key Design Decisions

### 1. **Sales-Only Mode**
Currently, pricing, change, and debt features are disabled in the UI layer while the database schema remains intact. This allows:
- Fast re-enablement of financial features later
- Code remains in place for future use
- No data model migration needed

### 2. **Multi-Tenant Architecture**
- All data scoped to `Company` FK
- Isolation by design; no cross-company data leakage
- Supports independent shop owners + their assistants

### 3. **Real vs Mock Routing**
- Production: Uses real Meta API via `set_whatsapp_client_source("meta")`
- Local dev/test: Uses MockWhatsAppClient; no actual SMS/API charges
- Allows simultaneous real and mock testing

### 4. **Draft Sales**
- Multi-message capture: "2 bread" → "1 coke" → confirm
- Prevents confirmation until user signals done
- Stale draft replacement: New multi-item message clears old draft

### 5. **Message Audit Trail**
- All WhatsAppMessage records (inbound/outbound) for compliance/debugging
- Links to Sale, UserProfile, WaitlistEntry for traceability

---

## Workflows at a Glance

### Onboarding
```
Unknown User (text) → Waitlist Entry Created → Language Choice → Admin Approves → 
Company + UserProfile Created → Welcome Message Sent
```

### Text Sale Recording
```
Known User sends "2 bread 1 coke" → OpenRouter parses → Create Sale/SaleItems → 
Receipt with Confirm/Fix buttons → User clicks Confirm → Sale marked confirmed
```

### Voice Sale Recording
```
Known User sends audio → ElevenLabs transcribes → Routes to text parser → Same as text flow
```

### Sales Query
```
User sends "how much did I sell today?" → OpenRouter detects query intent → 
Aggregate sales by date/product → Send summary to user
```

---

## Major Components Interaction

```
User (WhatsApp)
    ↓
Meta Cloud API
    ↓
Django Webhook View (validates signature)
    ↓
Message Handler (orchestrates)
    ├─→ Message Parser (NLP)
    │       ├─→ OpenRouter LLM
    │       └─→ Intent classification
    ├─→ Sales Service (persist)
    │       ├─→ Product CRUD
    │       └─→ Sale/SaleItem creation
    ├─→ Draft Manager (state)
    ├─→ Financial Tracker (change/debt)
    └─→ WhatsApp Client (send)
             ├─→ Receipt formatter
             ├─→ Button builder
             └─→ Meta Cloud API (send)
    ↓
Database
```

---

## Testing Architecture

**Unit Tests:** Parsing logic, formatting, model validators
**Integration Tests:** Full workflows (text sale, voice sale, query, confirmation, fix)
**Mock WhatsApp:** All testing uses MockWhatsAppClient to avoid API charges
**Pytest Framework:** Async support, database rollback per test

---

## Deployment Pipeline

```
Local Dev (ngrok + SQLite + MockClient)
    ↓ (commit + push)
Staging (Fly.io + PostgreSQL + Real Meta API)
    ↓ (run pytest, sanity tests)
Production (Fly.io + PostgreSQL)
```

---

## Future Enhancement Hooks

- **Re-enable Pricing:** Remove `PRICING_FEATURES_ENABLED` flag, restore prompts
- **Inventory Management:** Track stock levels per InventoryAdjustment
- **Analytics:** Aggregate sales, top products, customer frequency
- **Multi-language:** Add more locales (Zulu, Ndebele, Afrikaans)
- **SMS Fallback:** Route to Twilio if WhatsApp unavailable
- **Webhook Retry Logic:** Handle transient failures from Meta API

