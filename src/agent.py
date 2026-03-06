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

    # Example: "Washington, DC"
    if "," in lowered:
        city_part, state_part = [p.strip() for p in lowered.rsplit(",", 1)]
        state_abbr = STATE_NAME_TO_ABBR.get(state_part, state_part.upper())
        if state_abbr in US_STATES and city_part:
            return validate_city_or_throw(city_part), state_abbr

    # Example: "Washington DC" or "Austin Texas"
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
                "Collect required patient demographics, then confirm everything, then finish.\n\n"

                "Required fields to collect:\n"
                "first name\n"
                "last name\n"
                "date of birth in MM slash DD slash YYYY\n"
                "sex: Male, Female, Other, or Decline to Answer\n"
                "phone number: 10 digit U.S. number\n"
                "address line 1\n"
                "city\n"
                "state: 2 letter abbreviation\n"
                "zip code: 5 digits or ZIP plus 4\n\n"

                "Optional fields to offer after required fields:\n"
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
                "If the caller says start over, reset the draft and begin again from first name.\n"
                "After required fields are complete, ask:\n"
                "I can also collect your email, insurance information, emergency contact, and preferred language. Would you like to provide any of those?\n"
                "If yes, collect whichever optional fields they want.\n"
                "Before saving or finishing, read back all collected fields clearly and ask for final confirmation.\n"
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
        """Store a valid 10-digit U.S. phone number."""
        stored = parse_us_phone_or_throw(phoneNumber, "phone number")
        self.draft_patient.phoneNumber = stored
        self.draft_patient.pendingConfirmationField = "phoneNumber"
        self.draft_patient.confirmed = False
        return confirmation_payload("phoneNumber", f"phone number {speak_digits(stored)}")

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
    async def setInsuranceMemberId(self, context: RunContext, insuranceMemberId: str) -> dict[str, Any]:
        """Store insurance member ID, if provided."""
        stored = validate_member_id(insuranceMemberId)
        self.draft_patient.insuranceMemberId = stored
        self.draft_patient.pendingConfirmationField = "insuranceMemberId"
        self.draft_patient.confirmed = False
        return confirmation_payload("insuranceMemberId", f"insurance member ID {spell_for_voice(stored)}")

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
        Requires all required fields.
        Writes snake_case payload to a new JSON file, then POSTs it to the backend.
        """
        d = self.draft_patient

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

        out_dir = os.getenv("REGISTRATION_OUTPUT_DIR", "registrations")
        os.makedirs(out_dir, exist_ok=True)

        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = os.path.join(out_dir, f"patient-registration-{ts}.json")

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "created_at_utc": ts,
                    "patient": payload,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )

        logger.info("WROTE_REGISTRATION_FILE %s", filename)

        create_url = os.getenv("PATIENT_CREATE_URL")
        if not create_url:
            raise ToolError(
                "PATIENT_CREATE_URL is not set in .env.local. Please add the full POST endpoint URL."
            )

        backend_result = await asyncio.to_thread(
            post_patient_to_backend_or_throw,
            create_url,
            payload,
        )

        d.confirmed = True
        d.pendingConfirmationField = None
        logger.info("CREATED_PATIENT_VIA_BACKEND %s", backend_result)

        return {
            "ok": True,
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
        "Collect one field at a time. "
        "If a tool returns a confirmation prompt, say it exactly once and wait for the caller's answer. "
        "Do not confirm the same value twice. "
        "For names, repeat them naturally. Only spell them if the caller originally spelled them out or asks you to. "
        "If the caller gives city and state together, such as Washington, D C, extract both. "
        "If the caller says a full state name, normalize it to the 2-letter abbreviation. "
        "For phone numbers and ZIP codes, read digits individually. "
        "If something is rejected, explain exactly why and ask for that same field again. "
        "Then proceed through the required fields first: first name, last name, date of birth, sex, phone number, address line 1, city, state, zip code. "
        "After required fields, offer optional fields. "
        "Before finishing, read back everything and ask for final confirmation. "
        "If confirmed, call confirmRegistration and close politely."
    )

    try:
        session.generate_reply(instructions=kickoff)  # type: ignore[arg-type]
    except TypeError:
        await session.say(kickoff)


if __name__ == "__main__":
    cli.run_app(server)