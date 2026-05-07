# System Flows

This document uses Mermaid diagrams to show how the main pilot workflows connect.
Keep these diagrams high-level enough to stay useful as implementation details
change.

## System Context

```mermaid
flowchart LR
    Owner[Shop owner or assistant]
    Admin[Admin or operator]
    WhatsApp[Meta WhatsApp Cloud API]
    Django[Django app]
    DB[(PostgreSQL)]
    OpenRouter[OpenRouter LLM]
    ElevenLabs[ElevenLabs transcription]
    R2[Cloudflare R2]
    Sentry[Sentry]

    Owner <--> WhatsApp
    Admin <--> WhatsApp
    Admin --> Django
    WhatsApp --> Django
    Django --> WhatsApp
    Django <--> DB
    Django --> OpenRouter
    Django --> ElevenLabs
    Django --> R2
    Django --> Sentry
```

## Sender Routing

Every inbound WhatsApp message first goes through sender lookup. This decides
whether the system should onboard the user, hold them on the waitlist, or process
their message as an approved shop user.

```mermaid
flowchart TD
    A[Inbound WhatsApp message] --> B[Normalize phone number]
    B --> C{Sender exists?}
    C -->|UserProfile found| D[Known user flow]
    C -->|WaitlistEntry found| E[Waitlisted user flow]
    C -->|No record found| F[Create waitlist entry]
    F --> G[Ask user for language]
    F --> H[Notify admin with approve or reject buttons]
```

## Text Sale Flow

```mermaid
sequenceDiagram
    participant User as Shop user
    participant Meta as WhatsApp Cloud API
    participant Webhook as Django webhook
    participant Parser as OpenRouter parser
    participant Sales as Sales service
    participant DB as Database
    participant Bot as WhatsApp client

    User->>Meta: Sends text sale message
    Meta->>Webhook: POST /webhook/whatsapp/
    Webhook->>DB: Record inbound WhatsAppMessage
    Webhook->>DB: Load UserProfile and Company
    Webhook->>Parser: Parse intent, items, prices, currency
    Parser-->>Webhook: Structured sale result
    Webhook->>Sales: Create sale from parsed items
    Sales->>DB: Find or create Products
    Sales->>DB: Create ProductPrice records if needed
    Sales->>DB: Create Sale and SaleItems
    Webhook->>Bot: Send receipt with confirm/fix buttons
    Bot->>Meta: Send WhatsApp message
    Bot->>DB: Record outbound WhatsAppMessage
    Meta-->>User: Receipt appears in WhatsApp
```

## Voice Sale Flow

```mermaid
sequenceDiagram
    participant User as Shop user
    participant Meta as WhatsApp Cloud API
    participant Webhook as Django webhook
    participant Eleven as ElevenLabs
    participant R2 as Cloudflare R2
    participant Parser as OpenRouter parser
    participant Sales as Sales service
    participant DB as Database
    participant Bot as WhatsApp client

    User->>Meta: Sends voice note
    Meta->>Webhook: POST /webhook/whatsapp/
    Webhook->>DB: Record inbound audio WhatsAppMessage
    Webhook->>Meta: Download audio media
    Webhook->>Eleven: Transcribe audio
    Webhook->>R2: Store media for review/debugging
    Eleven-->>Webhook: Transcribed text
    Webhook->>DB: Save transcription
    Webhook->>Parser: Parse transcription
    Parser-->>Webhook: Structured sale result
    Webhook->>Sales: Create sale from parsed items
    Sales->>DB: Create/update products, prices, sale, sale items
    Webhook->>Bot: Send receipt with confirm/fix buttons
    Bot->>Meta: Send WhatsApp message
    Meta-->>User: Receipt appears in WhatsApp
```

## Waitlist Approval Flow

```mermaid
sequenceDiagram
    participant User as New user
    participant Meta as WhatsApp Cloud API
    participant Webhook as Django webhook
    participant DB as Database
    participant Admin as Admin/operator
    participant Bot as WhatsApp client

    User->>Meta: Sends first message
    Meta->>Webhook: POST /webhook/whatsapp/
    Webhook->>DB: Create WaitlistEntry
    Webhook->>Bot: Ask user to choose language
    Webhook->>Bot: Send admin approve/reject buttons
    Bot->>Meta: Send outbound messages
    Meta-->>User: Language prompt
    Meta-->>Admin: Approval request

    Admin->>Meta: Clicks approve
    Meta->>Webhook: Button response webhook
    Webhook->>DB: Mark WaitlistEntry approved
    Webhook->>DB: Create Company
    Webhook->>DB: Create Django User
    Webhook->>DB: Create owner UserProfile
    Webhook->>Bot: Notify admin
    Webhook->>Bot: Welcome approved user
    Meta-->>User: Welcome message
```

## Sale Button Flow

```mermaid
flowchart TD
    A[Receipt with buttons sent to user] --> B{User action}
    B -->|Confirm| C[Find confirmed sale by response message id]
    C --> D[Send acknowledgement]
    B -->|Fix / bot mistake| E[Find confirmed sale by response message id]
    E --> F[Mark sale cancelled]
    F --> G[Set flagged_as_bot_mistake]
    G --> H[Send bot mistake acknowledgement]
```

## Data Relationships

```mermaid
erDiagram
    Company ||--o{ UserProfile : has
    Company ||--o{ Product : owns
    Company ||--o{ Sale : records
    Company ||--o{ WhatsAppMessage : has
    UserProfile ||--o{ WhatsAppMessage : sends_or_receives
    WaitlistEntry ||--o{ WhatsAppMessage : relates_to
    WaitlistEntry }o--|| Company : creates
    WaitlistEntry }o--|| UserProfile : creates
    Product ||--o{ ProductPrice : has
    Product ||--o{ InventoryAdjustment : adjusted_by
    Product ||--o{ SaleItem : sold_as
    Sale ||--o{ SaleItem : contains
    Sale ||--o{ WhatsAppMessage : relates_to
```

## Pilot-Critical Path

The most important path to protect for Phase 1 is:

```mermaid
flowchart LR
    A[Approved shop user] --> B[Sends text or voice sale]
    B --> C[Message is recorded]
    C --> D[Sale is parsed]
    D --> E[Sale and items are stored]
    E --> F[User receives receipt]
    F --> G[Operator can review in admin]
```

If a proposed change does not improve or protect this path, it should usually go
to the improvement backlog until after the first pilot.
