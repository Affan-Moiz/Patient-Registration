import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Literal, Optional

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobProcess,
    RunContext,
    ToolError,
    cli,
    function_tool,
    inference,
    room_io,
)
from livekit.plugins import noise_cancellation, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

logger = logging.getLogger("patient-registration-agent")
logging.basicConfig(level=logging.INFO)

load_dotenv(".env.local")

Sex = Literal["Male", "Female", "Other", "Decline to Answer"]

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

STATE_NAME_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "d c": "DC",
    "dc": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


@dataclass
class DraftPatient:
    # Required
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    dateOfBirth: Optional[str] = None  # MM/DD/YYYY
    sex: Optional[Sex] = None
    phoneNumber: Optional[str] = None  # digits only
    addressLine1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None  # 2-letter
    zipCode: Optional[str] = None  # 5 or 5-4

    # Optional
    email: Optional[str] = None
    addressLine2: Optional[str] = None
    insuranceProvider: Optional[str] = None
    insuranceMemberId: Optional[str] = None
    preferredLanguage: Optional[str] = None
    emergencyContactName: Optional[str] = None
    emergencyContactPhone: Optional[str] = None

    # Conversation state
    confirmed: bool = False
    pendingConfirmationField: Optional[str] = None

    # Spelling hints for more natural confirmations
    firstNameWasSpelled: bool = False
    lastNameWasSpelled: bool = False
    emergencyContactNameWasSpelled: bool = False

    # Existing-patient / update flow
    existingPatientId: Optional[str] = None
    existingPatientData: Optional[dict[str, Any]] = None
    existingPatientIdentified: bool = False
    updateMode: bool = False


def normalize_whitespace(s: str) -> str:
    return " ".join(s.split()).strip()


def was_spelled_letter_by_letter(input_s: str) -> bool:
    cleaned = normalize_whitespace(input_s).replace("-", " ").replace("_", " ").replace(".", " ").upper()
    parts = [p for p in cleaned.split(" ") if p]
    return len(parts) >= 2 and all(len(p) == 1 and p.isalpha() for p in parts)


def maybe_join_spelled_letters(input_s: str) -> str:
    if not was_spelled_letter_by_letter(input_s):
        return input_s
    cleaned = normalize_whitespace(input_s).replace("-", " ").replace("_", " ").replace(".", " ").upper()
    parts = [p for p in cleaned.split(" ") if p]
    return "".join(parts)


def title_case_name(input_s: str) -> str:
    def tc_piece(piece: str) -> str:
        if not piece:
            return piece
        return piece[0].upper() + piece[1:].lower()

    words = input_s.split(" ")
    out_words = []
    for w in words:
        hy_parts = w.split("-")
        hy_out = []
        for hp in hy_parts:
            ap_parts = hp.split("'")
            ap_out = [tc_piece(p) for p in ap_parts]
            hy_out.append("'".join(ap_out))
        out_words.append("-".join(hy_out))
    return " ".join(out_words)


def validate_human_name_or_throw(raw: str, field_label: str) -> str:
    s = normalize_whitespace(raw)
    if len(s) < 1 or len(s) > 50:
        raise ToolError(f"{field_label} must be between 1 and 50 characters.")
    import re
    if not (re.fullmatch(r"[A-Za-z][A-Za-z' -]*[A-Za-z]", s) or re.fullmatch(r"[A-Za-z]", s)):
        raise ToolError(f"{field_label} can only include letters, spaces, hyphens, and apostrophes.")
    if re.search(r"[ '-]{2,}", s):
        raise ToolError(f"{field_label} looks malformed. Please say it again clearly.")
    return s


def validate_address_line_or_throw(raw: str, field_label: str) -> str:
    s = normalize_whitespace(raw)
    if len(s) < 1 or len(s) > 200:
        raise ToolError(f"{field_label} must be between 1 and 200 characters.")
    return s


def validate_city_or_throw(raw: str) -> str:
    s = normalize_whitespace(raw)
    if len(s) < 1 or len(s) > 100:
        raise ToolError("City must be between 1 and 100 characters.")
    import re
    if not (re.fullmatch(r"[A-Za-z][A-Za-z .'\-]*[A-Za-z]", s) or re.fullmatch(r"[A-Za-z]", s)):
        raise ToolError("City should only include letters, spaces, periods, hyphens, or apostrophes.")
    return title_case_name(s)


def validate_state_or_throw(raw: str) -> str:
    s = normalize_whitespace(raw)
    lowered = s.lower().replace(".", "")
    if lowered in STATE_NAME_TO_ABBR:
        return STATE_NAME_TO_ABBR[lowered]

    upper = s.upper()
    import re
    if re.fullmatch(r"[A-Z]{2}", upper) and upper in US_STATES:
        return upper

    raise ToolError("State must be a valid U.S. state, like California or CA.")


def parse_city_and_state(raw: str) -> tuple[str, Optional[str]]:
    s = normalize_whitespace(raw)
    lowered = s.lower().replace(".", "")
    lowered = lowered.replace("district of columbia", "dc")

    if "," in lowered:
        city_part, state_part = [p.strip() for p in lowered.rsplit(",", 1)]
        state_abbr = STATE_NAME_TO_ABBR.get(state_part, state_part.upper())
        if state_abbr in US_STATES and city_part:
            return validate_city_or_throw(city_part), state_abbr

    parts = lowered.split()
    if len(parts) >= 2:
        for take in (2, 1):
            candidate_state = " ".join(parts[-take:])
            state_abbr = STATE_NAME_TO_ABBR.get(candidate_state)
            if state_abbr in US_STATES:
                city_part = " ".join(parts[:-take]).strip()
                if city_part:
                    return validate_city_or_throw(city_part), state_abbr

    return validate_city_or_throw(s), None


def validate_zip_or_throw(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 5:
        return digits
    if len(digits) == 9:
        return f"{digits[:5]}-{digits[5:]}"
    raise ToolError(
        "ZIP code should be 5 digits, or 9 digits for ZIP plus 4. For example, 10001 or 10001-1234."
    )


def parse_us_phone_or_throw(raw: Any, field_label: str) -> str:
    if isinstance(raw, (int, float)):
        s = str(int(raw))
    elif isinstance(raw, str):
        s = raw
    else:
        raise ToolError(f"I couldn't understand that {field_label}. Please say a 10-digit U.S. number.")

    digits = "".join(ch for ch in s if ch.isdigit())

    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    if len(digits) < 10:
        raise ToolError(
            f"I only got {len(digits)} digits for the {field_label}. Please say all 10 digits, including area code."
        )
    if len(digits) > 10:
        raise ToolError(
            f"I heard too many digits for the {field_label}. Please say just the 10-digit U.S. number."
        )

    return digits


def validate_email_or_throw(raw: str) -> str:
    s = normalize_whitespace(raw)
    if len(s) > 254:
        raise ToolError("Email looks too long.")
    import re
    ok = re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", s) is not None
    if not ok:
        raise ToolError("That email does not look valid. Please say it again.")
    return s.lower()


def parse_dob_or_throw(raw: str) -> str:
    s = normalize_whitespace(raw)
    import re
    m = re.fullmatch(r"(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})", s)
    if not m:
        raise ToolError(
            "I need the date of birth in month, day, year format. For example, 04 slash 27 slash 1988."
        )

    mm = int(m.group(1))
    dd = int(m.group(2))
    yy = int(m.group(3))

    if yy < 100:
        yy = 2000 + yy if yy <= 29 else 1900 + yy

    if mm < 1 or mm > 12:
        raise ToolError("The month must be between 1 and 12.")
    if dd < 1 or dd > 31:
        raise ToolError("The day must be between 1 and 31.")

    import datetime as pydt
    try:
        date = pydt.date(yy, mm, dd)
    except ValueError:
        raise ToolError("That is not a valid calendar date.")

    today = pydt.date.today()
    if date > today:
        raise ToolError("The date of birth cannot be in the future.")
    if yy < today.year - 120:
        raise ToolError("That date of birth seems too far in the past. Please repeat it to confirm.")

    return f"{mm:02d}/{dd:02d}/{yy}"


def normalize_sex_or_throw(raw: str) -> Sex:
    s = normalize_whitespace(raw).lower()
    male = {"male", "man", "m", "boy", "he", "him"}
    female = {"female", "woman", "f", "girl", "she", "her"}
    other = {"other", "nonbinary", "non-binary", "nb", "intersex"}
    dta = {"decline", "decline to answer", "prefer not to say", "prefer not", "rather not say", "skip"}

    if s in male:
        return "Male"
    if s in female:
        return "Female"
    if s in other:
        return "Other"
    if s in dta:
        return "Decline to Answer"
    raise ToolError("For sex, please say male, female, other, or decline to answer.")


def validate_optional_free_text(raw: str, field_label: str, max_len: int = 120) -> str:
    s = normalize_whitespace(raw)
    if len(s) < 1:
        raise ToolError(f"{field_label} cannot be empty.")
    if len(s) > max_len:
        raise ToolError(f"{field_label} is too long.")
    return s


def validate_member_id(raw: str) -> str:
    s = normalize_whitespace(raw)
    if len(s) < 1 or len(s) > 50:
        raise ToolError("Insurance member ID must be between 1 and 50 characters.")
    import re
    if re.fullmatch(r"[A-Za-z0-9\-]+", s) is None:
        raise ToolError("Insurance member ID should be letters, numbers, or dashes only.")
    return s


def spell_for_voice(value: str) -> str:
    return " ".join(list(value.upper()))


def speak_digits(value: str) -> str:
    return " ".join(ch for ch in value if ch.isdigit())


def speak_zip(zip_code: str) -> str:
    digits = "".join(ch for ch in zip_code if ch.isdigit())
    if len(digits) == 9:
        return f"{' '.join(digits[:5])}, {' '.join(digits[5:])}"
    return " ".join(digits)


def speak_dob(dob: str) -> str:
    mm, dd, yyyy = dob.split("/")
    return f"{mm}, {dd}, {yyyy}"


def speak_email(value: str) -> str:
    return value.replace("@", " at ").replace(".", " dot ")


def confirmation_payload(field: str, spoken_value: str) -> dict[str, Any]:
    return {
        "ok": True,
        "field": field,
        "requires_confirmation": True,
        "confirmation_prompt": f"I heard {spoken_value}. Is that correct?",
    }


def get_patient_api_url_or_throw() -> str:
    base = os.getenv("PATIENT_API_URL")
    if not base:
        raise ToolError(
            "PATIENT_API_URL is not set in .env.local. Please add the patients endpoint URL."
        )
    return base.rstrip("/")


def draft_to_payload_snake_case(d: DraftPatient) -> dict[str, Any]:
    preferred_language = d.preferredLanguage or "English"

    payload: dict[str, Any] = {
        "first_name": d.firstName,
        "last_name": d.lastName,
        "date_of_birth": d.dateOfBirth,
        "sex": d.sex,
        "phone_number": d.phoneNumber,
        "address_line_1": d.addressLine1,
        "city": d.city,
        "state": d.state,
        "zip_code": d.zipCode,
        "preferred_language": preferred_language,
    }

    if d.email:
        payload["email"] = d.email
    if d.addressLine2:
        payload["address_line_2"] = d.addressLine2
    if d.insuranceProvider:
        payload["insurance_provider"] = d.insuranceProvider
    if d.insuranceMemberId:
        payload["insurance_member_id"] = d.insuranceMemberId
    if d.emergencyContactName:
        payload["emergency_contact_name"] = d.emergencyContactName
    if d.emergencyContactPhone:
        payload["emergency_contact_phone"] = d.emergencyContactPhone

    return payload


def normalize_backend_patient_to_draft(d: DraftPatient, patient: dict[str, Any]) -> None:
    d.existingPatientId = patient.get("patient_id")
    d.existingPatientData = patient

    d.firstName = patient.get("first_name")
    d.lastName = patient.get("last_name")
    d.dateOfBirth = patient.get("date_of_birth")
    d.sex = patient.get("sex")
    d.phoneNumber = patient.get("phone_number")
    d.addressLine1 = patient.get("address_line_1")
    d.addressLine2 = patient.get("address_line_2")
    d.city = patient.get("city")
    d.state = patient.get("state")
    d.zipCode = patient.get("zip_code")
    d.email = patient.get("email")
    d.insuranceProvider = patient.get("insurance_provider")
    d.insuranceMemberId = patient.get("insurance_member_id")
    d.preferredLanguage = patient.get("preferred_language") or "English"
    d.emergencyContactName = patient.get("emergency_contact_name")
    d.emergencyContactPhone = patient.get("emergency_contact_phone")


def build_update_payload_from_draft(d: DraftPatient) -> dict[str, Any]:
    current_payload = draft_to_payload_snake_case(d)
    existing = d.existingPatientData or {}

    mapping = {
        "first_name": existing.get("first_name"),
        "last_name": existing.get("last_name"),
        "date_of_birth": existing.get("date_of_birth"),
        "sex": existing.get("sex"),
        "phone_number": existing.get("phone_number"),
        "address_line_1": existing.get("address_line_1"),
        "address_line_2": existing.get("address_line_2"),
        "city": existing.get("city"),
        "state": existing.get("state"),
        "zip_code": existing.get("zip_code"),
        "email": existing.get("email"),
        "insurance_provider": existing.get("insurance_provider"),
        "insurance_member_id": existing.get("insurance_member_id"),
        "preferred_language": existing.get("preferred_language") or "English",
        "emergency_contact_name": existing.get("emergency_contact_name"),
        "emergency_contact_phone": existing.get("emergency_contact_phone"),
    }

    changed: dict[str, Any] = {}
    for key, value in current_payload.items():
        if mapping.get(key) != value:
            changed[key] = value

    optional_keys = [
        "email",
        "address_line_2",
        "insurance_provider",
        "insurance_member_id",
        "emergency_contact_name",
        "emergency_contact_phone",
    ]

    draft_null_candidates = {
        "email": d.email,
        "address_line_2": d.addressLine2,
        "insurance_provider": d.insuranceProvider,
        "insurance_member_id": d.insuranceMemberId,
        "emergency_contact_name": d.emergencyContactName,
        "emergency_contact_phone": d.emergencyContactPhone,
    }

    for key in optional_keys:
        if key in existing and existing.get(key) is not None and draft_null_candidates[key] is None:
            changed[key] = None

    return changed


def fetch_patient_by_phone_or_throw(base_url: str, phone: str) -> Optional[dict[str, Any]]:
    req = urllib.request.Request(
        url=f"{base_url}/by-phone/{phone}",
        headers={
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"raw_response": raw}

            if not parsed:
                return None

            if isinstance(parsed, dict) and "data" in parsed:
                return parsed.get("data")

            return parsed

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None

        error_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else None
        except json.JSONDecodeError:
            parsed_error = {"raw_response": error_body}

        raise ToolError(
            f"Backend returned HTTP {e.code} while looking up the phone number: {parsed_error}"
        )
    except urllib.error.URLError as e:
        raise ToolError(f"Could not reach the backend to check the phone number: {e.reason}")
    except Exception as e:
        raise ToolError(f"Unexpected error while checking the phone number: {str(e)}")


def post_patient_to_backend_or_throw(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"raw_response": raw}

            return {
                "status_code": resp.getcode(),
                "response": parsed,
            }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else None
        except json.JSONDecodeError:
            parsed_error = {"raw_response": error_body}

        raise ToolError(
            f"Backend returned HTTP {e.code} while creating the patient record: {parsed_error}"
        )
    except urllib.error.URLError as e:
        raise ToolError(f"Could not reach the backend to create the patient record: {e.reason}")
    except Exception as e:
        raise ToolError(f"Unexpected error while creating the patient record: {str(e)}")


def update_patient_in_backend_or_throw(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="PUT",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"raw_response": raw}

            return {
                "status_code": resp.getcode(),
                "response": parsed,
            }

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed_error = json.loads(error_body) if error_body else None
        except json.JSONDecodeError:
            parsed_error = {"raw_response": error_body}

        raise ToolError(
            f"Backend returned HTTP {e.code} while updating the patient record: {parsed_error}"
        )
    except urllib.error.URLError as e:
        raise ToolError(f"Could not reach the backend to update the patient record: {e.reason}")
    except Exception as e:
        raise ToolError(f"Unexpected error while updating the patient record: {str(e)}")


class PatientRegistrationAgent(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "You are a professional, warm, patient registration voice assistant.\n\n"

                "Tone and speaking style:\n"
                "Speak naturally, warmly, and a little slowly.\n"
                "Use short sentences.\n"
                "Pause slightly between important items.\n"
                "Do not sound rushed.\n"
                "Ask one question at a time.\n"
                "Avoid jargon.\n"
                "Never mention tool names or internal systems.\n\n"

                "Core behavior:\n"
                "Sensitive fields must be confirmed exactly once after they are stored.\n"
                "Sensitive fields are: first name, last name, date of birth, phone number, email, state, ZIP code, insurance member ID, emergency contact name, and emergency contact phone.\n"
                "Use the tool-provided confirmation prompt when available.\n"
                "Do not ask the same confirmation twice.\n"
                "For names, say the name naturally when confirming.\n"
                "Only spell the name letter by letter if the caller originally spelled it out, if the name seems unusual, or if the caller asks you to spell it.\n"
                "For phone numbers, ZIP codes, and IDs, read the digits or characters back clearly.\n"
                "For date of birth, read it back clearly in month, day, year.\n"
                "If the caller corrects you, thank them, update the value, and confirm again once.\n\n"

                "Returning patient workflow:\n"
                "Ask for phone number early.\n"
                "When a phone number is provided, the system may find an existing patient record.\n"
                "If an existing record is found, say the exact confirmation prompt from the tool and ask if that is the caller.\n"
                "If the caller says yes, ask whether they want to update any details.\n"
                "If they want updates, ask only for the fields they want to change, one at a time.\n"
                "Do not re-collect unchanged fields unless the caller wants to change them.\n"
                "If the caller says no, continue as a new registration.\n\n"

                "Error handling:\n"
                "If a value is rejected, tell the caller exactly why in plain English.\n"
                "Then tell them what format you need.\n"
                "Then ask only for that same field again.\n"
                "Do not just say invalid or not valid.\n\n"

                "Address behavior:\n"
                "If the caller provides multiple address parts together, such as city and state in one utterance, extract both instead of asking them to split it up.\n"
                "If the caller says a full state name, normalize it to the 2-letter abbreviation.\n"
                "For example, if the caller says Washington, D C, store city as Washington and state as DC.\n\n"

                "Your goal:\n"
                "For a new patient, collect required patient demographics, then optional fields if desired, then confirm everything, then finish.\n"
                "For a returning patient who wants to update details, collect only the fields they want to change, then read back only the updated fields plus enough identity context to avoid mistakes, then finish.\n\n"

                "Required fields for new registration:\n"
                "first name\n"
                "last name\n"
                "date of birth in MM slash DD slash YYYY\n"
                "sex: Male, Female, Other, or Decline to Answer\n"
                "phone number: 10 digit U.S. number\n"
                "address line 1\n"
                "city\n"
                "state: 2 letter abbreviation\n"
                "zip code: 5 digits or ZIP plus 4\n\n"

                "Optional fields to offer after required fields for new registration:\n"
                "email\n"
                "address line 2\n"
                "insurance provider\n"
                "insurance member ID\n"
                "preferred language (default English)\n"
                "emergency contact name\n"
                "emergency contact phone\n\n"

                "Process rules:\n"
                "When the user provides a field, call the matching tool to store it.\n"
                "If the tool returns a confirmation_prompt, say that prompt exactly once and wait for the caller's answer.\n"
                "Do not re-confirm the same value in different wording.\n"
                "After the caller says yes, move on.\n"
                "Always handle corrections.\n"
                "Do not ask the caller to repeat information you already have.\n"
                "If the caller says start over, reset the draft and begin again.\n"
                "After required fields are complete for a new registration, ask:\n"
                "I can also collect your email, insurance information, emergency contact, and preferred language. Would you like to provide any of those?\n"
                "If yes, collect whichever optional fields they want.\n"
                "Before saving or finishing, read back the relevant collected information clearly and ask for final confirmation.\n"
                "If the user confirms, call confirmRegistration, then close politely."
            )
        )
        self.draft_patient = DraftPatient()

    @function_tool()
    async def setFirstName(self, context: RunContext, firstName: str) -> dict[str, Any]:
        """Store the patient's first name. Accept spelled-out names like D A V I S. Use for corrections too."""
        spelled = was_spelled_letter_by_letter(firstName)
        joined = maybe_join_spelled_letters(firstName)
        validated = validate_human_name_or_throw(joined, "First name")
        stored = title_case_name(validated)

        self.draft_patient.firstName = stored
        self.draft_patient.firstNameWasSpelled = spelled
        self.draft_patient.pendingConfirmationField = "firstName"
        self.draft_patient.confirmed = False

        spoken = spell_for_voice(stored) if spelled else stored
        return confirmation_payload("firstName", f"first name {spoken}")

    @function_tool()
    async def setLastName(self, context: RunContext, lastName: str) -> dict[str, Any]:
        """Store the patient's last name. Accept spelled-out names like D A V I S. Use for corrections too."""
        spelled = was_spelled_letter_by_letter(lastName)
        joined = maybe_join_spelled_letters(lastName)
        validated = validate_human_name_or_throw(joined, "Last name")
        stored = title_case_name(validated)

        self.draft_patient.lastName = stored
        self.draft_patient.lastNameWasSpelled = spelled
        self.draft_patient.pendingConfirmationField = "lastName"
        self.draft_patient.confirmed = False

        spoken = spell_for_voice(stored) if spelled else stored
        return confirmation_payload("lastName", f"last name {spoken}")

    @function_tool()
    async def setDateOfBirth(self, context: RunContext, dateOfBirth: str) -> dict[str, Any]:
        """Store date of birth. Must be valid and not in the future. Normalize to MM/DD/YYYY."""
        stored = parse_dob_or_throw(dateOfBirth)
        self.draft_patient.dateOfBirth = stored
        self.draft_patient.pendingConfirmationField = "dateOfBirth"
        self.draft_patient.confirmed = False
        return confirmation_payload("dateOfBirth", f"date of birth {speak_dob(stored)}")

    @function_tool()
    async def setSex(self, context: RunContext, sex: str) -> dict[str, Any]:
        """Store sex. Normalize to Male, Female, Other, or Decline to Answer."""
        self.draft_patient.sex = normalize_sex_or_throw(sex)
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setPhoneNumber(self, context: RunContext, phoneNumber: str) -> dict[str, Any]:
        """Store a valid 10-digit U.S. phone number. Also check whether an existing patient already has this number."""
        stored = parse_us_phone_or_throw(phoneNumber, "phone number")
        self.draft_patient.phoneNumber = stored
        self.draft_patient.pendingConfirmationField = "phoneNumber"
        self.draft_patient.confirmed = False

        base_url = get_patient_api_url_or_throw()
        existing = await asyncio.to_thread(
            fetch_patient_by_phone_or_throw,
            base_url,
            stored,
        )

        if existing:
            self.draft_patient.existingPatientId = existing.get("patient_id")
            self.draft_patient.existingPatientData = existing
            self.draft_patient.existingPatientIdentified = False
            self.draft_patient.updateMode = False

            first_name = existing.get("first_name") or "the patient"
            last_name = existing.get("last_name") or ""
            full_name = normalize_whitespace(f"{first_name} {last_name}")
            dob = existing.get("date_of_birth")

            if dob:
                prompt = f"I found a patient record for {full_name}, date of birth {speak_dob(dob)}. Is that you?"
            else:
                prompt = f"I found a patient record for {full_name}. Is that you?"

            return {
                "ok": True,
                "field": "phoneNumber",
                "existing_patient_found": True,
                "requires_confirmation": True,
                "confirmation_prompt": prompt,
            }

        return confirmation_payload("phoneNumber", f"phone number {speak_digits(stored)}")

    @function_tool()
    async def confirmExistingPatientIdentity(self, context: RunContext, isExistingPatient: bool) -> dict[str, Any]:
        """Confirm whether the caller matches the patient record found by phone number."""
        if not self.draft_patient.existingPatientData:
            raise ToolError("There is no existing patient record waiting for confirmation.")

        if isExistingPatient:
            self.draft_patient.existingPatientIdentified = True
            self.draft_patient.updateMode = True
            normalize_backend_patient_to_draft(self.draft_patient, self.draft_patient.existingPatientData)

            first_name = self.draft_patient.firstName or "there"
            return {
                "ok": True,
                "identified": True,
                "message": (
                    f"Thanks, {first_name}. I have your record. "
                    "Would you like to update any of your details today?"
                ),
            }

        self.draft_patient.existingPatientId = None
        self.draft_patient.existingPatientData = None
        self.draft_patient.existingPatientIdentified = False
        self.draft_patient.updateMode = False

        return {
            "ok": True,
            "identified": False,
            "message": "Okay. We will continue as a new registration.",
        }

    @function_tool()
    async def setEmail(self, context: RunContext, email: str) -> dict[str, Any]:
        """Store email if the user provides one. Must be valid email format."""
        stored = validate_email_or_throw(email)
        self.draft_patient.email = stored
        self.draft_patient.pendingConfirmationField = "email"
        self.draft_patient.confirmed = False
        return confirmation_payload("email", f"email {speak_email(stored)}")

    @function_tool()
    async def setAddressLine1(self, context: RunContext, addressLine1: str) -> dict[str, Any]:
        """Store street address line 1."""
        self.draft_patient.addressLine1 = validate_address_line_or_throw(addressLine1, "Address line 1")
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setAddressLine2(self, context: RunContext, addressLine2: str) -> dict[str, Any]:
        """Store address line 2 such as apartment or suite, if provided."""
        v = normalize_whitespace(addressLine2)
        if not v:
            raise ToolError("Address line 2 cannot be empty if provided.")
        if len(v) > 200:
            raise ToolError("Address line 2 is too long.")
        self.draft_patient.addressLine2 = v
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def clearAddressLine2(self, context: RunContext) -> dict[str, Any]:
        """Clear address line 2."""
        self.draft_patient.addressLine2 = None
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setCity(self, context: RunContext, city: str) -> dict[str, Any]:
        """Store city. If the utterance also contains a state, infer and store both."""
        parsed_city, parsed_state = parse_city_and_state(city)
        self.draft_patient.city = parsed_city
        if parsed_state:
            self.draft_patient.state = parsed_state
        self.draft_patient.confirmed = False
        return {"ok": True, "city": parsed_city, "state": parsed_state}

    @function_tool()
    async def setState(self, context: RunContext, state: str) -> dict[str, Any]:
        """Store a U.S. state using either full name or 2-letter abbreviation, like California or CA."""
        stored = validate_state_or_throw(state)
        self.draft_patient.state = stored
        self.draft_patient.pendingConfirmationField = "state"
        self.draft_patient.confirmed = False
        return confirmation_payload("state", f"state {spell_for_voice(stored)}")

    @function_tool()
    async def setZipCode(self, context: RunContext, zipCode: str) -> dict[str, Any]:
        """Store ZIP code as 5 digits or ZIP plus 4."""
        stored = validate_zip_or_throw(zipCode)
        self.draft_patient.zipCode = stored
        self.draft_patient.pendingConfirmationField = "zipCode"
        self.draft_patient.confirmed = False
        return confirmation_payload("zipCode", f"ZIP code {speak_zip(stored)}")

    @function_tool()
    async def setInsuranceProvider(self, context: RunContext, insuranceProvider: str) -> dict[str, Any]:
        """Store insurance provider name, if provided."""
        self.draft_patient.insuranceProvider = validate_optional_free_text(
            insuranceProvider, "Insurance provider", 120
        )
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def clearInsuranceProvider(self, context: RunContext) -> dict[str, Any]:
        """Clear insurance provider."""
        self.draft_patient.insuranceProvider = None
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setInsuranceMemberId(self, context: RunContext, insuranceMemberId: str) -> dict[str, Any]:
        """Store insurance member ID, if provided."""
        stored = validate_member_id(insuranceMemberId)
        self.draft_patient.insuranceMemberId = stored
        self.draft_patient.pendingConfirmationField = "insuranceMemberId"
        self.draft_patient.confirmed = False
        return confirmation_payload("insuranceMemberId", f"insurance member ID {spell_for_voice(stored)}")

    @function_tool()
    async def clearInsuranceMemberId(self, context: RunContext) -> dict[str, Any]:
        """Clear insurance member ID."""
        self.draft_patient.insuranceMemberId = None
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setPreferredLanguage(self, context: RunContext, preferredLanguage: str) -> dict[str, Any]:
        """Store preferred language, if provided. Default is English if not provided."""
        self.draft_patient.preferredLanguage = validate_optional_free_text(
            preferredLanguage, "Preferred language", 50
        )
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setEmergencyContactName(self, context: RunContext, emergencyContactName: str) -> dict[str, Any]:
        """Store emergency contact full name, if provided."""
        spelled = was_spelled_letter_by_letter(emergencyContactName)
        joined = maybe_join_spelled_letters(emergencyContactName)
        validated = validate_human_name_or_throw(joined, "Emergency contact name")
        stored = title_case_name(validated)

        self.draft_patient.emergencyContactName = stored
        self.draft_patient.emergencyContactNameWasSpelled = spelled
        self.draft_patient.pendingConfirmationField = "emergencyContactName"
        self.draft_patient.confirmed = False

        spoken = spell_for_voice(stored) if spelled else stored
        return confirmation_payload("emergencyContactName", f"emergency contact name {spoken}")

    @function_tool()
    async def clearEmergencyContactName(self, context: RunContext) -> dict[str, Any]:
        """Clear emergency contact name."""
        self.draft_patient.emergencyContactName = None
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def setEmergencyContactPhone(self, context: RunContext, emergencyContactPhone: str) -> dict[str, Any]:
        """Store emergency contact phone as a valid 10-digit U.S. number, if provided."""
        stored = parse_us_phone_or_throw(emergencyContactPhone, "emergency contact phone number")
        self.draft_patient.emergencyContactPhone = stored
        self.draft_patient.pendingConfirmationField = "emergencyContactPhone"
        self.draft_patient.confirmed = False
        return confirmation_payload(
            "emergencyContactPhone",
            f"emergency contact phone number {speak_digits(stored)}",
        )

    @function_tool()
    async def clearEmergencyContactPhone(self, context: RunContext) -> dict[str, Any]:
        """Clear emergency contact phone."""
        self.draft_patient.emergencyContactPhone = None
        self.draft_patient.confirmed = False
        return {"ok": True}

    @function_tool()
    async def getDraft(self, context: RunContext) -> dict[str, Any]:
        """Get the current draft patient data for read-back confirmation."""
        return {"ok": True, "draft": asdict(self.draft_patient)}

    @function_tool()
    async def resetDraft(self, context: RunContext) -> dict[str, Any]:
        """Reset draft patient data if the user wants to start over."""
        self.draft_patient = DraftPatient()
        return {"ok": True}

    @function_tool()
    async def clearPendingConfirmation(self, context: RunContext) -> dict[str, Any]:
        """Clear the current pending confirmation after the caller confirms a value."""
        self.draft_patient.pendingConfirmationField = None
        return {"ok": True}

    @function_tool()
    async def confirmRegistration(self, context: RunContext) -> dict[str, Any]:
        """
        Call only after reading back all collected info and the user confirms it is correct.

        For new patients:
        - requires all required fields
        - writes snake_case payload to a new JSON file
        - POSTs to the backend

        For returning patients in update mode:
        - builds a changed-fields payload
        - writes the changed payload to a new JSON file
        - PUTs to the backend /patients/:id
        """
        d = self.draft_patient

        out_dir = os.getenv("REGISTRATION_OUTPUT_DIR", "registrations")
        os.makedirs(out_dir, exist_ok=True)

        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        base_url = get_patient_api_url_or_throw()

        if d.updateMode and d.existingPatientIdentified and d.existingPatientId:
            update_payload = build_update_payload_from_draft(d)

            if not update_payload:
                raise ToolError("There are no changes to save yet.")

            filename = os.path.join(out_dir, f"patient-update-{d.existingPatientId}-{ts}.json")

            with open(filename, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "created_at_utc": ts,
                        "mode": "update",
                        "patient_id": d.existingPatientId,
                        "patient": update_payload,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )

            logger.info("WROTE_PATIENT_UPDATE_FILE %s", filename)

            backend_result = await asyncio.to_thread(
                update_patient_in_backend_or_throw,
                f"{base_url}/{d.existingPatientId}",
                update_payload,
            )

            d.confirmed = True
            d.pendingConfirmationField = None
            logger.info("UPDATED_PATIENT_VIA_BACKEND %s", backend_result)

            return {
                "ok": True,
                "mode": "update",
                "file": filename,
                "patient_id": d.existingPatientId,
                "patient": update_payload,
                "backend": backend_result,
            }

        if not all(
            [
                d.firstName,
                d.lastName,
                d.dateOfBirth,
                d.sex,
                d.phoneNumber,
                d.addressLine1,
                d.city,
                d.state,
                d.zipCode,
            ]
        ):
            raise ToolError("Cannot confirm yet. Missing one or more required fields.")

        payload = draft_to_payload_snake_case(d)
        filename = os.path.join(out_dir, f"patient-registration-{ts}.json")

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "created_at_utc": ts,
                    "mode": "create",
                    "patient": payload,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        logger.info("WROTE_REGISTRATION_FILE %s", filename)

        backend_result = await asyncio.to_thread(
            post_patient_to_backend_or_throw,
            base_url,
            payload,
        )

        d.confirmed = True
        d.pendingConfirmationField = None
        logger.info("CREATED_PATIENT_VIA_BACKEND %s", backend_result)

        return {
            "ok": True,
            "mode": "create",
            "file": filename,
            "patient": payload,
            "backend": backend_result,
        }


server = AgentServer()


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


server.setup_fnc = prewarm


@server.rtc_session(agent_name="Personal-info-agent-dev")
async def entrypoint(ctx: JobContext):
    ctx.log_context_fields = {"room": ctx.room.name}

    session = AgentSession(
        stt=inference.STT(model="deepgram/nova-3", language="en"),
        llm=inference.LLM(model="openai/gpt-4.1-mini"),
        tts=inference.TTS(
            model="deepgram/aura-2",
            voice="athena",
        ),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
        preemptive_generation=True,
    )

    await session.start(
        agent=PatientRegistrationAgent(),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=lambda params: (
                    noise_cancellation.BVCTelephony()
                    if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                    else noise_cancellation.BVC()
                ),
            ),
        ),
    )

    await ctx.connect()

    kickoff = (
        "Greet the caller warmly and begin patient registration. "
        "Speak naturally and a little slowly. "
        "Ask for the phone number early. "
        "When the caller gives a phone number, call the phone number tool immediately. "
        "If the tool says an existing patient was found, say the confirmation prompt exactly once and wait for the answer. "
        "If the caller confirms they are that patient, ask whether they want to update any details. "
        "If they want updates, ask only for the details they want to change, one at a time. "
        "If they are not that patient, continue as a new registration. "
        "For new registrations, proceed through the required fields: first name, last name, date of birth, sex, phone number if still needed, address line 1, city, state, zip code. "
        "If a tool returns a confirmation prompt, say it exactly once and wait for the caller's answer. "
        "Do not confirm the same value twice. "
        "For names, repeat them naturally. Only spell them if the caller originally spelled them out or asks you to. "
        "If the caller gives city and state together, such as Washington, D C, extract both. "
        "If the caller says a full state name, normalize it to the 2-letter abbreviation. "
        "For phone numbers and ZIP codes, read digits individually. "
        "If something is rejected, explain exactly why and ask for that same field again. "
        "After required fields for a new patient, offer optional fields. "
        "Before finishing, read back everything relevant and ask for final confirmation. "
        "If confirmed, call confirmRegistration and close politely."
    )

    try:
        session.generate_reply(instructions=kickoff)  # type: ignore[arg-type]
    except TypeError:
        await session.say(kickoff)


if __name__ == "__main__":
    cli.run_app(server)