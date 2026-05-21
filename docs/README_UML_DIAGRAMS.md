# PlantUML Diagrams - Auto Tuck Shop System

This directory contains comprehensive UML diagrams for the Auto Tuck Shop WhatsApp bot system in PlantUML format.

## Files

### 0. `uml_unified_overview.puml` - Unified System Overview
**Purpose:** One consolidated diagram that combines the most important parts of use cases, runtime flow, components, and core data relationships.

**Content:**
- Actors and external APIs (Owner/Assistant/Admin, Meta, OpenRouter, ElevenLabs)
- End-to-end processing flow (webhook -> parser -> intent routing -> response)
- Major architecture components in the Django app
- Core entities and high-value relationships (Company, UserProfile, Sale, SaleItem, financial and message logging models)
- Confirmation/Fix lifecycle for sale status

**Use this to:**
- Get a fast, shared understanding of the full system in one view
- Onboard new team members
- Align product/engineering discussions before diving into detailed diagrams
- Review feature impact across flow, architecture, and data

---

### 1. `uml_usecase.puml` - Use Case Diagram
**Purpose:** Shows all actors, use cases, and their interactions

**Content:**
- Actors: Shop Owner, Shop Assistant, Admin, Bot, External APIs
- Use Cases: Onboarding, Text/Voice sale recording, Confirmation, Change/Debt tracking, Sales query, Add Assistant
- Relationships: Which actors can perform which use cases

**Use this to:**
- Understand high-level system functionality
- Identify all workflows
- Map stakeholder responsibilities
- Plan feature development

---

### 2. `uml_classes.puml` - Class Diagram  
**Purpose:** Shows domain models, attributes, and relationships

**Content:**
- 22 main domain classes organized by package:
  - Organization: Company, UserProfile, WaitlistEntry
  - Catalog: Category, Product, ProductPrice, InventoryAdjustment
  - Sales: Sale, SaleItem, DraftSale, DraftSaleItem
  - Financial: ChangeRecord, DebtorRecord, CustomerCreditBalance, CustomerCreditTransaction
  - WhatsApp: WhatsAppMessage, PendingAction
- 1-to-many and 1-to-1 relationships
- Enum fields showing status values

**Use this to:**
- Understand the data model
- Plan database migrations
- Debug data integrity issues
- Design new features that interact with models

---

### 3. `uml_sequence_message_processing.puml` - Sequence Diagram
**Purpose:** Shows message flow from user → API → bot → database → response

**Content:**
- Actors: User, Meta API, Django Webhook, Handler, Parser, OpenRouter, Sales Service, Database, WhatsApp Client
- Steps: Webhook validation → Parsing → Intent routing (Sale|Query|Add Assistant|Other) → Response
- Button responses: Confirm/Fix actions
- Error handling branches (conceptual)

**Use this to:**
- Trace message processing end-to-end
- Understand the async/sync flow
- Debug message handling issues
- Verify all steps occur in correct order

---

### 4. `uml_components.puml` - Component Diagram
**Purpose:** Shows system architecture, layers, and inter-component communication

**Content:**
- External APIs: Meta, OpenRouter, ElevenLabs, R2
- Django layers: Webhook, Parsing, Business Logic, Models, Localization
- Supporting Infrastructure: Database, Deployment (Fly.io), Monitoring (Sentry)
- Testing: Mock WhatsApp, Pytest
- Data flow between components

**Use this to:**
- Understand system architecture
- Plan infrastructure upgrades
- Identify integration points
- Troubleshoot cross-component issues

---

### 5. `uml_database_schema.puml` - Database Schema Diagram
**Purpose:** Shows database tables, columns, types, and relationships

**Content:**
- 20+ entity-relationship definitions
- Primary keys, foreign keys, unique constraints
- Column types and nullable fields
- Enums (e.g., role, status, direction)
- Indexes and constraints

**Use this to:**
- Write SQL queries
- Plan database migrations
- Verify referential integrity
- Optimize indexes

---

## How to Render PlantUML Diagrams

### Option 1: Online PlantUML Editor
1. Go to [PlantUML Online Editor](http://www.plantuml.com/plantuml/uml/SyfFKj2rKt3CoKnELR1Io4ZDoSa70000)
2. Copy the contents of the diagram you want (e.g., `uml_usecase.puml`, `uml_classes.puml`)
3. Paste into the editor
4. View the rendered diagram

### Option 2: Local PlantUML CLI
```bash
# Install PlantUML (requires Java)
brew install plantuml

# Generate PNG from .puml
plantuml uml_usecase.puml -o ../diagrams/png

# Generate SVG (recommended for web)
plantuml uml_usecase.puml -tsvg -o ../diagrams/svg
```

### Option 3: VS Code Extension
1. Install "PlantUML" extension
2. Open any diagram file (e.g., `uml_usecase.puml`) → Right-click → "Preview"
3. View rendered diagram in split pane

### Option 4: IntelliJ / PyCharm
- Built-in PlantUML support
- Right-click file → "Diagrams" → "Show Diagram"

---

## Diagram Relationships

```
Unified Overview (recommended starting point)
    ↓ (drill down by concern)
Use Cases | Sequence | Components | Classes | Database Schema
```

---

## Quick Reference: What Diagram to Use

| Question | Diagram |
|----------|---------|
| I want one consolidated system view | **Unified Overview** |
| What workflows exist? | **Use Case** |
| How are objects structured? | **Class** |
| What happens when a user sends a message? | **Sequence** |
| How do code modules communicate? | **Component** |
| What tables and relationships exist? | **Database Schema** |
| I'm a stakeholder, show me system overview | **Use Case + Component** |
| I'm debugging a bug in message processing | **Sequence** |
| I'm adding a new feature to sales | **Class + Sequence** |
| I'm optimizing database queries | **Database Schema** |

---

## Updates

These diagrams were generated based on the codebase as of **16 May 2026**.

Update them when:
- New use cases are added (UC11+)
- New domain classes are created
- Major workflow changes occur
- Database schema is migrated
- New components/APIs are integrated

---

## Exporting for Documentation

All diagrams can be exported as:
- **PNG** - Good for presentations, reduced quality
- **SVG** - Scalable, good for web, smaller file size
- **PDF** - Print-friendly, vector quality
- **ASCII** - Text-only, for embedding in code comments

Example:
```bash
plantuml uml_*.puml -tpng -o ./diagrams/png/
plantuml uml_*.puml -tsvg -o ./diagrams/svg/
```

