````md
# Voice AI Patient Registration Agent

A voice-based AI patient registration system that answers a real U.S. phone number, collects patient demographics through natural conversation, confirms the information with the caller, and saves the final record through a backend REST API.

This project was built for a Voice AI / Conversational AI Engineer technical assessment focused on telephony, LLM orchestration, validation, persistence, and API design. The required behavior includes natural conversation, confirmation before save, and field-specific error handling.

## Live Demo

**Phone number to call:** `+1 (484) 295-0167`

When you call, the agent:
- greets you warmly
- collects required patient fields one at a time
- confirms sensitive fields out loud before moving on
- explains validation problems in plain English
- reads back the full registration before saving
- submits the confirmed payload to the backend API

---

# System Architecture

```text
Phone Caller
    │
    ▼
LiveKit Telephony Number
    │
    ▼
LiveKit Voice Agent
(STT + LLM + TTS + tool calls)
    │
    ▼
Backend REST API
(Node.js service)
    │
    ▼
Persistent Database
````

## End-to-End Flow

1. A caller dials the LiveKit phone number.
2. The LiveKit voice agent answers and starts a natural intake conversation.
3. The agent collects required demographics first.
4. For sensitive fields such as names, date of birth, phone number, state, and ZIP code, the agent repeats the value back and asks for confirmation.
5. If the caller gives invalid input, the agent explains what was wrong and asks again only for that field.
6. After required fields are complete, the agent offers optional fields.
7. Before saving, the agent reads back everything collected and asks for final confirmation.
8. Once confirmed, the agent writes a local JSON registration file and sends a `POST /patients` request to the backend.
9. The backend validates and persists the patient record.
10. The caller hears a completion message and the call ends gracefully.

---

# What the Agent Now Does Better

## Improved confirmation behavior

The updated agent explicitly confirms sensitive fields after the user provides them. Examples:

* names are spelled back clearly when needed
* phone numbers are read digit by digit
* ZIP codes are read back clearly
* date of birth is repeated before moving forward

This was added to make the phone experience more reliable and closer to a human intake coordinator.

## Better validation feedback

If the caller gives an invalid answer, the agent does not just reject it generically. It explains why. Examples:

* phone number has too few digits
* ZIP code is not 5 digits or ZIP+4
* date of birth is in the wrong format
* date of birth is in the future
* state is not a valid 2-letter U.S. abbreviation

## More natural voice behavior

The voice agent has been tuned to sound less robotic and less rushed by:

* slowing TTS speed slightly
* using a calmer speaking style
* adding a small pause between utterances
* using short, conversational prompts

---

# Tech Stack

| Layer               | Technology                         |
| ------------------- | ---------------------------------- |
| Telephony           | LiveKit Telephony                  |
| Voice Agent Runtime | LiveKit Agents (Python)            |
| STT                 | Deepgram Nova-3                    |
| LLM                 | OpenAI GPT-4.1 mini                |
| TTS                 | Cartesia Sonic-3                   |
| VAD                 | Silero                             |
| Turn Detection      | LiveKit multilingual turn detector |
| Backend             | Node.js REST API                   |
| Database            | Persistent storage behind backend  |
| Local Tunneling     | ngrok                              |
| Deployment          | LiveKit Cloud                      |

---

# Patient Data Collected

## Required fields

* first name
* last name
* date of birth
* sex
* phone number
* address line 1
* city
* state
* ZIP code

## Optional fields

* email
* address line 2
* insurance provider
* insurance member ID
* preferred language
* emergency contact name
* emergency contact phone

The agent collects required fields first, then offers optional fields.

---

# Repository Structure

```text
.
├── agent/
│   └── ... LiveKit Python voice agent
├── backend/
│   └── ... Node.js REST API and persistence
├── registrations/
│   └── ... local JSON registration logs written by the agent
└── README.md
```

---

# Voice Agent Details

## Conversational behavior

The agent is designed to:

* ask one question at a time
* handle caller corrections naturally
* confirm sensitive values before proceeding
* re-prompt only the field that failed validation
* read back all information before saving

## Confirmation examples

Examples of how the agent behaves:

* “I heard first name J A N E. Is that correct?”
* “I heard phone number 2 1 2 5 5 5 0 1 9 8. Is that correct?”
* “I heard ZIP code 1 0 0 0 1. Is that correct?”

## Validation examples

Examples of field-specific feedback:

* “I only got 7 digits for the phone number. Please say all 10 digits, including area code.”
* “ZIP code should be 5 digits, or 9 digits for ZIP plus 4.”
* “I need the date of birth in month, day, year format. For example, 04 slash 27 slash 1988.”

## Current model/session configuration

The voice pipeline uses:

* `deepgram/nova-3` for speech-to-text
* `openai/gpt-4.1-mini` for reasoning/tool use
* `cartesia/sonic-3` for speech output
* `silero` VAD
* multilingual turn detection

The TTS is tuned for a calmer experience with slower speech and better pacing.

---

# Backend API

The voice agent submits confirmed patient registrations to the backend.

## Example endpoints

### List patients

```http
GET /patients
```

Optional query parameters may include:

```http
/patients?last_name=Doe
/patients?phone_number=1234567890
/patients?date_of_birth=01/01/1990
```

### Get patient by ID

```http
GET /patients/:id
```

### Create patient

```http
POST /patients
```

### Update patient

```http
PUT /patients/:id
```

### Soft delete patient

```http
DELETE /patients/:id
```

Deletes logically by setting a `deleted_at` timestamp instead of hard deleting.

---

# Example Payload Sent by the Agent

When the caller confirms the registration, the agent converts the draft into snake_case and sends a payload similar to:

```json
{
  "first_name": "Jane",
  "last_name": "Doe",
  "date_of_birth": "01/15/1990",
  "sex": "Female",
  "phone_number": "2125550198",
  "address_line_1": "123 Main Street",
  "city": "Brooklyn",
  "state": "NY",
  "zip_code": "11201",
  "preferred_language": "English",
  "email": "jane.doe@example.com",
  "insurance_provider": "Aetna",
  "insurance_member_id": "ABC12345",
  "emergency_contact_name": "John Doe",
  "emergency_contact_phone": "9175550101"
}
```

If optional values are not collected, they are omitted from the request except `preferred_language`, which defaults to `English`.

---

# Local Registration File Output

Before posting to the backend, the agent also writes a JSON file locally for observability/debugging.

Example filename:

```text
registrations/patient-registration-20260306T143000Z.json
```

Each file contains:

* UTC creation timestamp
* final patient payload sent to the backend

---

# Environment Variables

## Agent `.env.local`

Example:

```env
LIVEKIT_API_KEY=<livekit_api_key>
LIVEKIT_API_SECRET=<livekit_api_secret>
LIVEKIT_URL=<livekit_url>
OPENAI_API_KEY=<openai_api_key>
REGISTRATION_OUTPUT_DIR=registrations
PATIENT_CREATE_URL=<public_backend_post_endpoint>
```

Notes:

* the code writes registration JSON files to `REGISTRATION_OUTPUT_DIR`
* the patient create endpoint should point to your public backend endpoint
* credentials should never be hardcoded in source control

## Backend environment

Use whatever your backend requires, for example:

```env
PORT=3000
DATABASE_URL=<database_connection_string>
```

---

# Backend Setup

## 1. Install dependencies

From the backend project directory:

```bash
npm install
```

## 2. Start the backend

Example:

```bash
npm run dev
```

or

```bash
node server.js
```

## 3. Expose the backend publicly with ngrok

```bash
ngrok config add-authtoken <YOUR_AUTH_TOKEN>
ngrok http 3000
```

This gives you a public URL such as:

```text
https://abcd-1234.ngrok-free.app
```

Your patient creation endpoint would then typically be:

```text
https://abcd-1234.ngrok-free.app/patients
```

Set that URL in the agent environment.

---

# Agent Setup

## 1. Install dependencies

From the agent project directory, install Python dependencies using your preferred environment manager.

Example with `uv` or `pip` depending on your setup.

## 2. Configure `.env.local`

Example:

```env
PATIENT_CREATE_URL=<backend_URL>
REGISTRATION_OUTPUT_DIR=registrations
LIVEKIT_API_KEY=<livekit_api_key>
LIVEKIT_API_SECRET=<livekit_api_secret>
LIVEKIT_URL=<livekit_url>
OPENAI_API_KEY=<openai_api_key>
```

## 3. Authenticate with LiveKit Cloud

```bash
lk cloud auth
```

## 4. Deploy the agent

```bash
lk agent create
```

The deployed session is configured with the LiveKit agent name:

```text
Personal-info-agent-dev
```

---

# LiveKit Telephony Setup

## 1. Acquire a phone number

Provision a LiveKit telephony number in the LiveKit dashboard.

## 2. Create a dispatch rule

Create a dispatch rule for individual calls and bind it to the deployed agent.

## 3. Call the number

Use:

```text
+1 (484) 295-0167
```

to test the full patient intake flow.

---

# Observability

The system logs useful information to stdout, including:

* registration file creation
* final payload sent to the backend
* backend response or error
* agent/runtime logs

The agent also writes local JSON registration artifacts to disk for easier debugging.

---

# Known Limitations / Trade-offs

* Telephony latency can still vary based on network quality and provider conditions.
* The voice experience is improved, but LLM-driven conversations can still occasionally vary in phrasing.
* Duplicate patient detection is not yet fully implemented in the voice flow.
* The code currently uses a concrete patient creation endpoint and should rely on environment configuration in production.

---

# Architecture Decisions

## Why LiveKit

LiveKit provides a strong fit for this challenge because it combines:

* telephony integration
* voice agent orchestration
* model/tool calling
* deployable cloud runtime

This reduces glue code and lets the project focus on conversation quality and backend integration.

## Why REST for persistence

Using a backend REST API keeps the voice agent decoupled from the database. That improves:

* separation of concerns
* testability
* backend validation
* future extensibility for dashboards or admin tooling

## Why ngrok during development

ngrok is a fast, practical choice for a take-home assessment because it:

* exposes a local backend quickly
* avoids spending time on full hosting setup
* makes end-to-end voice-to-backend testing possible within a short time window

---

# Next Steps

Planned improvements:

1. detect existing patients by phone number and offer update flow
2. add transcript or call summary persistence
3. improve automated tests for backend and agent validation logic
4. add a small admin UI for browsing patient records

```
```
