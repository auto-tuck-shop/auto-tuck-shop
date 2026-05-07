# System Architecture

Auto Tuck Shop is a Django application centered around WhatsApp message intake,
AI-assisted sale parsing, and admin-supported pilot operations.

For visual diagrams of the major flows and data relationships, see
[System Flows](flows.md).

## Main Components

- `apps.whatsapp`: Meta webhook handling, message recording, button actions, and
  WhatsApp client integration.
- `apps.core`: companies, user profiles, waitlist entries, approval logic, and
  pilot metrics.
- `apps.catalog`: products, categories, price history, and derived stock.
- `apps.sales`: sales, sale items, sale creation, status, and bot mistake flags.
- `apps.inventory`: stock adjustments.
- `services.openrouter`: LLM parsing through OpenRouter.
- `services.elevenlabs`: voice transcription.
- `services.storage`: Cloudflare R2 media storage.
- `services.whatsapp.mock_client`: staging/test WhatsApp replacement.

## Text Sale Flow

1. Meta sends a webhook to `/webhook/whatsapp/`.
2. `WhatsAppWebhookView` verifies/parses the payload.
3. The sender phone number is normalized.
4. The sender is classified as:
   - known user,
   - waitlisted user,
   - unknown user.
5. Known user text is recorded as an inbound `WhatsAppMessage`.
6. The unified parser asks the LLM for intent, items, quantities, prices, and
   currency.
7. Sale creation finds or creates products, updates price history when needed,
   and creates a `Sale` plus `SaleItem` rows.
8. The bot replies with a receipt and buttons:
   - confirm,
   - fix / bot mistake.

## Voice Sale Flow

1. Meta sends an audio message webhook.
2. The system records the inbound audio message.
3. The WhatsApp client downloads the media from Meta.
4. ElevenLabs transcribes the audio.
5. The transcription is stored on the `WhatsAppMessage`.
6. The transcribed text follows the same sale parsing and creation flow as a text
   message.
7. Media can be uploaded to R2 for later review/debugging.

## Waitlist Flow

1. Unknown sender sends a message.
2. A `WaitlistEntry` is created.
3. The user is asked to pick a language.
4. Admin receives an approval/rejection message with buttons.
5. Approval creates:
   - `Company`,
   - Django `User`,
   - `UserProfile` with owner role.
6. The user receives a welcome message and can begin recording sales.

## Assistant Flow

Approved shop owners can ask the bot to add an assistant phone number. The LLM
classifies the request as `add_assistant`, extracts the phone number, and the
system creates an assistant `UserProfile` for the same company.

## Data Model Summary

- `Company`: one shop/business.
- `UserProfile`: a WhatsApp user attached to a company as owner or assistant.
- `WaitlistEntry`: pending/rejected/approved onboarding request.
- `Product`: shop-specific catalog item.
- `ProductPrice`: historical price for a product.
- `InventoryAdjustment`: manual stock movement.
- `Sale`: one recorded sale event.
- `SaleItem`: product, quantity, and unit price within a sale.
- `WhatsAppMessage`: inbound/outbound audit record.

## External Services

- Meta WhatsApp Business Cloud API: message receive/send and media download.
- OpenRouter: intent detection and sale extraction.
- ElevenLabs: audio transcription.
- Cloudflare R2: media storage.
- Fly.io: hosting.
- PostgreSQL: production database.
- Sentry: error/performance monitoring.

## Important Design Constraint

The first pilot should treat WhatsApp as the product interface. Admin pages and
metrics exist to support the pilot, not to replace the WhatsApp workflow.
