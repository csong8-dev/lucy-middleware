# Lucy Caller ID Lookup Middleware

Real-time caller lookup service for Lucy (OAO Restaurant AI Voice Agent).

## What it does

When a call comes in to Lucy (`+447888871838`), ElevenLabs fires a **Conversation Initiation Client Data Webhook** to this service **before Lucy speaks**. This service:

1. Receives the caller's phone number from ElevenLabs
2. Looks up the contact in GoHighLevel (OAO Warrington sub-account)
3. Returns dynamic variables to ElevenLabs:
   - `caller_history` — name + visit summary for returning guests
   - `greeting` — personalised greeting if known, standard if new

## Endpoint

```
POST /caller-lookup
```

### Request (from ElevenLabs)
```json
{
  "caller_id": "+447712345678",
  "called_number": "+447888871838"
}
```

### Response (to ElevenLabs)
```json
{
  "dynamic_variables": {
    "caller_history": "John Smith is a returning guest with 3 visits on record.",
    "greeting": "Hello John, lovely to hear from you again — this is Lucy at OAO Restaurant, how can I help you today?"
  }
}
```

## Environment Variables

| Variable | Description |
|---|---|
| `GHL_API_KEY` | GoHighLevel API key (Bearer token) |
| `GHL_LOCATION_ID` | GHL sub-account location ID (default: `YOHjoiCFRkHFJV8uA3tl`) |
| `PORT` | Port to run on (set automatically by Railway/Render) |

## Deployment

### Railway (recommended)
1. Push to GitHub
2. Connect repo to Railway
3. Set `GHL_API_KEY` environment variable
4. Deploy — Railway auto-detects Python and uses `Procfile`

### Render
1. Push to GitHub
2. Create new Web Service on Render
3. Connect repo, set `GHL_API_KEY` env var
4. Deploy

## Health Check

```
GET /health
```
Returns `{"status": "ok", "service": "lucy-caller-lookup"}`
