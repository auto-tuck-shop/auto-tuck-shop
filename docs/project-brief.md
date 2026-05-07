# Project Brief

Auto Tuck Shop is a WhatsApp assistant for small tuck shops. The first product
goal is simple: help shop owners record sales quickly using the device and habit
they already have, WhatsApp.

## Core User Problem

Many small shop owners do not consistently record every sale. Manual notebooks,
spreadsheets, and end-of-day reconciliation are easy to skip during busy shop
hours. The pilot version should make recording a sale feel as easy as sending a
message.

## Current Product Shape

A shop owner or assistant sends a WhatsApp message such as:

```text
sold 2 cokes for $1 each and 3 breads for 50c
```

The system should:

1. Receive the WhatsApp message.
2. Identify the sender and their shop.
3. Parse the sale using AI.
4. Create products/prices when needed.
5. Record the sale and sale items.
6. Reply with a receipt-style confirmation and action buttons.

Voice notes are also supported: the system downloads the audio, transcribes it,
then runs the same sale-recording flow.

## People In The System

- Shop owner: primary pilot user, owns one shop account.
- Assistant: optional shop worker who can record sales for the same shop.
- Admin/operator: approves pilot users, monitors messages, and helps resolve
  setup or support issues.

## Non-Negotiable For Pilot One

The first pilot is not about a complete retail platform. It is about reliable
sale capture.

The system must be trustworthy for:

- Onboarding a small number of pilot shops.
- Recording text-based sales.
- Recording voice-note sales where transcription succeeds.
- Preserving message and sale history for review.
- Letting users flag a bot mistake.
- Giving operators enough admin visibility to support the pilot.

Everything else is secondary unless it protects those flows.
