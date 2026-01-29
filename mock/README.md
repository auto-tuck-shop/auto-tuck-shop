# Auto Tuck Shop - WhatsApp Mock

## Running the Mock

```bash
cd mock
python3 -m http.server 8080
```

Then open http://localhost:8080

## Scenarios

Three demo scenarios are available:

### Scenario 1: Voice Sales Recording (Assistant View)
`?scenario=1`

Shows:
- Assistant recording sales via voice memo (tap the mic button)
- Bot transcribes and confirms the sale
- Owner receives per-sale notification in real-time

### Scenario 2: Weekly Summary (Owner View)
`?scenario=2`

Shows:
- Weekly summary with revenue and profit
- Top sellers breakdown
- Pricing recommendations (bread margin issue)
- Restocking insights

### Scenario 3: Inventory Alerts
`?scenario=3`

Shows:
- Expiration date tracker (milk, bread expiring)
- Low stock alerts with restock recommendations
- Price change detection from suppliers
- Margin protection suggestions

## Features Demonstrated

- Recording sales via voice memo
- Weekly summary (top sellers, headline revenue/profit)
- Pricing updates and margin calculator
- Restocking insights
- Expiration date tracker
- Assistant view vs owner view
- Per-sale notifications (narration mentions daily option too)
