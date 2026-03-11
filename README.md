# Voice AI Patient Registration Agent

A voice-based AI patient registration system that answers a real U.S. phone number, collects patient demographics through natural conversation, confirms the information with the caller, and saves the final record through a backend REST API.

This project was built for a **Voice AI / Conversational AI Engineer technical assessment** focused on telephony, LLM orchestration, validation, persistence, and API design. The required behavior includes natural conversation, confirmation before save, and field-specific error handling.

---

# Live Demo

**Phone number to call:** `+1 (484) 295-0167`

When you call, the agent:

* greets you warmly
* collects patient demographics through natural conversation
* confirms sensitive information before moving forward
* checks whether the caller already exists in the system
* allows returning patients to update their details
* explains validation issues clearly
* reads back the final record before saving

---

# System Architecture

```
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
(Node.js service hosted on Render)
    │
    ▼
Persistent Database
```

---

# End-to-End Flow

## New Patient Flow

1. A caller dials the LiveKit phone number.
2. The LiveKit voice agent answers and begins a natural conversation.
3. The agent asks for the caller's **phone number early in the interaction**.
4. The agent queries the backend to check if a patient already exists with that phone number.
5. If no existing record is found, the agent continues the **new patient registration flow**.
6. The agent collects required demographic fields.
7. Sensitive values are confirmed before proceeding.
8. Optional fields are offered after required fields.
9. The agent reads back all collected information.
10. After confirmation, the agent sends the final payload to the backend.
11. The backend validates and persists the patient record.
12. The caller hears a completion message and the call ends.

---

## Returning Patient Flow

If the backend **finds a patient with the provided phone number**:

1. The agent retrieves the stored patient information.
2. The agent asks the caller to confirm their identity.

Example:

> “I found a patient record with this phone number for Jane Doe. Is that you?”

3. If the caller confirms:

   * The agent asks if they would like to **update any details**.
   * The caller may change fields such as address, insurance information, or contact details.
   * The updated data is sent to the backend.

4. If the caller says **no**:

   * The agent assumes a **different person is using the phone number**.
   * A **new patient registration flow** begins.

---

# What the Agent Does Well

## Returning patient detection

The agent checks whether a caller already exists using their **phone number**.

This enables:

* returning patient recognition
* updating existing records
* reducing duplicate patient entries

---

## Improved confirmation behavior

Sensitive fields are explicitly confirmed before moving forward.

Examples:

* names may be spelled back when needed
* phone numbers are read digit by digit
* ZIP codes are repeated
* date of birth is confirmed

Example:

> “I heard phone number 2 1 2 5 5 5 0 1 9 8. Is that correct?”

---

## Better validation feedback

Invalid inputs trigger **clear explanations** rather than generic errors.

Examples:

* phone number missing digits
* ZIP code incorrect length
* invalid state abbreviation
* date of birth in the future

Example:

> “I only got 7 digits for the phone number. Please say all 10 digits including the area code.”

---

## More natural voice behavior

The voice experience was tuned to sound more conversational by:

* slowing the speech rate slightly
* using a calmer voice style
* adding small pauses between prompts
* asking short, human-like questions

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
| Backend API         | Node.js REST API                   |
| Database            | Persistent storage behind backend  |
| Hosting             | Render                             |
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

---

## Optional fields

* email
* address line 2
* insurance provider
* insurance member ID
* preferred language
* emergency contact name
* emergency contact phone

The agent collects **required fields first**, then offers optional fields.

---

# Repository Structure

```
.
├── agent/
│   └── LiveKit Python voice agent
├── registrations/
│   └── local JSON registration logs written by the agent
└── README.md
```

The backend service is hosted in a **separate repository**:

```
https://github.com/Affan-Moiz/Patient-Backend
```

This separation keeps the **voice orchestration layer independent from backend persistence logic**.

---

# Voice Agent Details

## Conversational behavior

The agent is designed to:

* ask one question at a time
* detect returning patients
* handle caller corrections naturally
* confirm sensitive values before proceeding
* re-prompt only the field that failed validation
* read back all information before saving

---

## Confirmation examples

Examples of spoken confirmations:

* “I heard first name J A N E. Is that correct?”
* “I heard phone number 2 1 2 5 5 5 0 1 9 8. Is that correct?”
* “I heard ZIP code 1 0 0 0 1. Is that correct?”

---

## Validation examples

Examples of field-specific feedback:

* “I only got 7 digits for the phone number. Please say all 10 digits including the area code.”
* “ZIP code should be 5 digits or 9 digits for ZIP plus 4.”
* “I need the date of birth in month, day, year format. For example 04 slash 27 slash 1988.”

---

# Backend API

The backend API is implemented in a separate repository and deployed on **Render**.

Backend repository:

```
https://github.com/Affan-Moiz/Patient-Backend
```

Production backend endpoint:

```
https://<your-render-service>.onrender.com
```

The voice agent interacts with this backend to:

* check for existing patients by phone number
* create new patient records
* update existing patient information

---

# Example API Endpoints

### List patients

```
GET /patients
```

Optional filters:

```
/patients?last_name=Doe
/patients?phone_number=1234567890
/patients?date_of_birth=01/01/1990
```

---

### Get patient by ID

```
GET /patients/:id
```

---

### Create patient

```
POST /patients
```

---

### Update patient

```
PUT /patients/:id
```

---

### Soft delete patient

```
DELETE /patients/:id
```

Deletes logically by setting a `deleted_at` timestamp rather than permanently removing the record.

---

# Example Payload Sent by the Agent

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

If optional values are not collected they are omitted except `preferred_language`, which defaults to **English**.

---

# Local Registration File Output

Before sending data to the backend, the agent writes a **local JSON artifact** for observability.

Example filename:

```
registrations/patient-registration-20260306T143000Z.json
```

Each file contains:

* UTC creation timestamp
* final patient payload sent to the backend

---

# Environment Variables

## Agent `.env.local`

Example configuration:

```
LIVEKIT_API_KEY=<livekit_api_key>
LIVEKIT_API_SECRET=<livekit_api_secret>
LIVEKIT_URL=<livekit_url>

OPENAI_API_KEY=<openai_api_key>

REGISTRATION_OUTPUT_DIR=registrations

PATIENT_CREATE_URL=https://<render-service>.onrender.com/patients
PATIENT_LOOKUP_URL=https://<render-service>.onrender.com/patients
```

Notes:

* `PATIENT_LOOKUP_URL` is used for **checking if a patient already exists**
* `PATIENT_CREATE_URL` is used for **creating new patient records**
* credentials should **never be committed to source control**

---

# Agent Setup

Install Python dependencies using your preferred environment manager.

Example:

```
pip install -r requirements.txt
```

Configure `.env.local`.

Authenticate with LiveKit Cloud:

```
lk cloud auth
```

Deploy the agent:

```
lk agent create
```

The deployed session uses the LiveKit agent name:

```
Personal-info-agent-dev
```

---

# LiveKit Telephony Setup

1. Acquire a LiveKit telephony number in the LiveKit dashboard.
2. Create a dispatch rule for incoming calls.
3. Bind the dispatch rule to the deployed agent.
4. Call the number to test the full patient intake flow.

---

# Observability

The system logs useful information including:

* registration JSON file creation
* final payload sent to the backend
* backend responses
* agent runtime logs

Local JSON artifacts help with **debugging and monitoring conversations**.

---

# Known Limitations / Trade-offs

* Telephony latency can vary based on network quality.
* LLM-driven conversations may vary slightly in phrasing.
* Caller identity verification currently relies only on **phone number confirmation**.
* Duplicate detection beyond phone number is not yet implemented.

---

# Architecture Decisions

## Why LiveKit

LiveKit provides:

* telephony integration
* voice agent orchestration
* model and tool calling
* deployable cloud runtime

This reduces infrastructure complexity and allows focus on the **conversation and backend integration**.

---

## Why REST between agent and backend

Using a REST API provides:

* separation between voice orchestration and persistence
* easier backend testing
* flexibility to build dashboards or admin tools later
* independent scaling of services

---

## Why Render for backend hosting

Render was chosen because it:

* provides simple cloud deployment for Node.js services
* integrates easily with GitHub
* provides stable HTTPS endpoints
* eliminates the need for development tunnels like ngrok

---

# Next Steps

Potential improvements:

1. stronger identity verification for returning patients
2. transcript and call summary persistence
3. automated tests for backend and agent validation logic
4. small admin UI for browsing patient records
5. duplicate patient detection beyond phone number
