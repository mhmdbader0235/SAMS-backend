"""
Randomised black-box QA suite: 50 real user processes against a live SchoolDesk stack.

WHAT THIS IS
------------
50 pytest cases that drive the deployed application the way the Vue frontend
does -- real HTTP through the gateway, real Bearer tokens from
`POST /api/v1/auth/login`, the `X-Tenant-ID` header the SPA always sends, and no
access to the database or to in-process app objects. If a case passes here, a
human clicking the same buttons gets the same result.

The 50 cases are the three kinds asked for:

  1. DIRECT (25) -- one user, one action, the thing they opened the app to do.
                    "A parent opens the published-trips list." Fast coverage of
                    every screen the app actually has.
  2. DEEP   (15) -- one goal, several steps, and the rules checked from the
                    outside: state-machine order, role boundaries, tenant
                    isolation, cost arithmetic, PII masking. Every deep case
                    asserts the happy path AND the way the app must say "no".
  3. FULL   (10) -- whole journeys, several users handing work to each other:
                    teacher drafts a trip -> manager prices it and approves ->
                    teacher publishes -> student asks for a seat ->
                    parent approves and pays -> trip lead approves the roster ->
                    feedback. Plus the branches that happen in real life
                    (rejection and resubmit, manager override, free trips,
                    parent refusal, cancellation, a brand-new school's day one).

RANDOMISATION
-------------
Every run provisions a fresh throwaway school: random school name, random
curriculum shape, random staff / students / parents with random names, and each
of the 50 cases picks its actors at random from that population. One seed drives
the whole run; it is printed in the header and replayed with `QA_SEED=<n>`.

RUNNING
-------
    cd back
    python run.py                        # the stack must be up
    .venv/Scripts/pytest tests/e2e -v            # all 50
    .venv/Scripts/pytest tests/e2e -v -m qa_full # only the 10 full journeys
    .venv/Scripts/pytest tests/e2e -v -m "qa_direct or qa_deep"

If the API is not reachable the module skips, so a plain `pytest tests/` in CI is
unaffected.

ENVIRONMENT
-----------
    QA_BASE_URL   default http://127.0.0.1:9080   the gateway the SPA talks to
    QA_SEED       default random                  replay a run exactly
    QA_E2E=0      force-skip even when the API is up

NO EMAIL DEPENDENCY
-------------------
Nothing in here uses `POST /api/v1/auth/invitations`: an invitation is delivered
by email to a real mailbox, which a test cannot open. Accounts are created the
way an operator creates them in-app instead -- the platform operator bootstraps
with the super_admin passphrase, staff and pupils are created directly with a
password (`/students/teachers`, `/students/managers`, `/students`), one staff
account is promoted to school_admin through `PUT /auth/users/{id}/permissions`,
and parents self-register with the shared passphrase. Every actor in the suite
can therefore log in immediately.

The finance role was retired from the app (manager now owns resource costing,
ticket pricing, and subsidy) -- there is no /students/finance endpoint and no
finance actor anywhere below.

SIDE EFFECTS
------------
The suite creates its own tenants (`qa_<seed>` and, for the day-one journey,
`qa_<seed>_new`) plus one control-plane super_admin. It never writes to
tenant_a / tenant_b or to any pre-existing school. Nothing is deleted
automatically; the tenant ids are printed at the end of the run so they can be
dropped deliberately, e.g.

    docker exec -i doumind-db psql -U admin -d doumind_control \
        -c 'DROP SCHEMA "qa_<seed>" CASCADE;'
"""

from __future__ import annotations

import itertools
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field

import httpx
import pytest

# =============================================================================
# Configuration
# =============================================================================

BASE_URL = os.getenv("QA_BASE_URL", "http://127.0.0.1:9080").rstrip("/")
SEED = int(os.getenv("QA_SEED") or (int(time.time()) % 1_000_000))
ENABLED = os.getenv("QA_E2E", "1") != "0"

# The seed decides every random CHOICE; the run tag keeps identities unique.
# Keeping them apart is what makes `QA_SEED=<n>` replayable -- a replay picks the
# same sections, actors and prices, but provisions its own tenant and its own
# email addresses instead of colliding with the run it is reproducing.
RUN_ID = f"{int(time.time()) % 100000:05d}{os.getpid() % 1000:03d}"
EMAIL_DOMAIN = "qa-doumind.com"
PASSWORD = "QaPass123!"
# The platform operator has its own dedicated bootstrap code, separate from the
# shared staff self-registration passphrases: a shared passphrase must never be
# able to mint cross-tenant super_admin access (F-01).
SUPER_ADMIN_CODE = os.getenv("SUPER_ADMIN_BOOTSTRAP_CODE", "sd-platform-bootstrap-2026")
STAFF_CODE = "regester123"  # shared staff/parent self-registration passphrase
TIMEOUT = 30.0

CREATED_TENANTS: list[str] = []

# The suite renders its own per-scenario trace; httpx's INFO log would bury it.
logging.getLogger("httpx").setLevel(logging.WARNING)

pytestmark = pytest.mark.qa_e2e


# =============================================================================
# Realistic random data pools
# =============================================================================

FIRST_NAMES = [
    "Layla",
    "Omar",
    "Nour",
    "Yousef",
    "Salma",
    "Rami",
    "Dana",
    "Karim",
    "Hana",
    "Tariq",
    "Maya",
    "Ziad",
    "Lina",
    "Adam",
    "Rana",
    "Sami",
    "Jana",
    "Faris",
    "Aisha",
    "Bilal",
    "Emma",
    "Lucas",
    "Sofia",
    "Noah",
    "Amelia",
    "Ethan",
]
LAST_NAMES = [
    "Haddad",
    "Khoury",
    "Nasser",
    "Darwish",
    "Odeh",
    "Mansour",
    "Barakat",
    "Sayegh",
    "Zaid",
    "Halabi",
    "Ayoub",
    "Rashid",
    "Fakhoury",
    "Sultan",
    "Bishara",
    "Attallah",
    "Sharif",
    "Qaddoura",
]
SCHOOL_WORDS = [
    "Alnoor",
    "Cedar",
    "Amman Modern",
    "Jubilee",
    "Petra",
    "Baraka",
    "Horizon",
    "Al-Manara",
]
SCHOOL_KINDS = ["International School", "Academy", "Bilingual School", "Grammar School"]
CITIES = ["Amman", "Irbid", "Zarqa", "Aqaba", "Madaba"]
STREETS = ["Zahran St", "Mecca St", "Rainbow St", "Abdoun Circle", "Gardens St", "University Rd"]

TRIP_TITLES = [
    "Trip to the Royal Automobile Museum",
    "Petra Field Study",
    "Jerash Roman Ruins Visit",
    "Dead Sea Ecology Day",
    "Children's Museum Science Day",
    "Ajloun Forest Walk",
    "Amman Citadel History Walk",
    "Aqaba Marine Centre Visit",
    "Wadi Rum Astronomy Night",
    "National Library Reading Day",
    "Planetarium Day",
    "Olive Harvest Farm Visit",
]
VENUES = [
    "Royal Automobile Museum, Amman",
    "Petra Visitor Centre",
    "Jerash Archaeological Park",
    "Dead Sea Panoramic Complex",
    "Children's Museum Jordan",
    "Ajloun Forest Reserve",
    "Amman Citadel",
    "Aqaba Marine Science Station",
    "Wadi Rum Protected Area",
]
FEEDBACK_NOTES = [
    "Well organised, the kids loved it.",
    "Bus was late but the visit was great.",
    "Good value for the ticket price.",
    "Please plan more of these.",
    "The guide was excellent with the younger grades.",
]
REJECTION_REASONS = [
    "Ticket price too high for this grade -- please re-cost.",
    "Date clashes with mid-term assessment week.",
    "Supervisor ratio is below policy for this age band.",
    "Transport budget for this term is already committed.",
]
CURRICULA = [
    ("UK", ["Year 1", "Year 2", "Year 3", "Year 4", "Year 5"]),
    ("International", ["Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5"]),
]


_EMAIL_SEQ = itertools.count(1)
_TRIP_SEQ = itertools.count(1)


def email_for(kind: str, name: str, tenant_suffix: str = "") -> str:
    """A plausible school email, unique per run.

    Two people in one school can genuinely share a name, so the address carries a
    sequence number as well -- without it a random collision would fail
    provisioning with a duplicate-email 500 instead of testing anything.
    """
    first, last = name.lower().split()[0], name.lower().split()[-1]
    seq = next(_EMAIL_SEQ)
    return f"{first}.{last}.{kind}{tenant_suffix}.{RUN_ID}{seq}@{EMAIL_DOMAIN}"


# =============================================================================
# HTTP layer -- one thin client that behaves like front/src/api.js
# =============================================================================


@dataclass
class Call:
    actor: str
    method: str
    path: str
    status: int
    ms: int
    detail: str

    def __str__(self) -> str:
        tail = f" {self.detail}" if self.detail else ""
        return f"    [{self.actor:<18}] {self.method:<6} {self.path:<50} -> {self.status} ({self.ms}ms){tail}"


class Trace:
    """Per-scenario call log, attached to every assertion failure."""

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def reset(self) -> None:
        self.calls = []

    def render(self) -> str:
        return "\n".join(str(c) for c in self.calls) or "    (no calls recorded)"


class Response:
    def __init__(self, raw: httpx.Response, trace: Trace, actor: str):
        self.raw = raw
        self.status = raw.status_code
        self.trace = trace
        self.actor = actor
        try:
            self.body = raw.json()
        except Exception:
            self.body = None

    @property
    def detail(self) -> str:
        if isinstance(self.body, dict) and "detail" in self.body:
            return str(self.body["detail"])
        return ""

    def expect(self, *codes: int, because: str = "") -> Response:
        if self.status not in codes:
            wanted = " or ".join(str(c) for c in codes)
            raise AssertionError(
                f"{self.raw.request.method} {self.raw.request.url.path} as {self.actor}: "
                f"expected {wanted}, got {self.status}. {because}\n"
                f"  response: {self.raw.text[:500]}\n"
                f"  --- what this user did ---\n{self.trace.render()}"
            )
        return self

    def json(self):
        return self.body


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def endpoint_of(method: str, path: str) -> str:
    """Collapse a request path back to its route template, e.g.
    `POST /api/v1/events/12/resources` -> `POST /api/v1/events/{id}/resources`.
    Used to report which slice of the API a run actually exercised."""
    parts = [
        "{id}" if (seg.isdigit() or _UUID_RE.fullmatch(seg)) else seg for seg in path.split("/")
    ]
    return f"{method} {'/'.join(parts)}"


class Api:
    def __init__(self, base_url: str = BASE_URL):
        self.client = httpx.Client(base_url=base_url, timeout=TIMEOUT)
        self.trace = Trace()
        self.total_calls = 0
        self.endpoints: set[str] = set()

    def close(self) -> None:
        self.client.close()

    def coverage(self) -> list[str]:
        return sorted(self.endpoints)

    def call(
        self,
        method: str,
        path: str,
        *,
        actor: Actor | None = None,
        token: str | None = None,
        tenant: str | None = None,
        label: str | None = None,
        json_body=None,
        params=None,
    ) -> Response:
        headers: dict[str, str] = {}
        auth = token if token is not None else (actor.token if actor else None)
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        # The SPA always sends the active tenant; the backend only honours it for
        # super_admin (see app/core/dependencies.py).
        if tenant:
            headers["X-Tenant-ID"] = tenant
        who = label or (actor.label if actor else "anonymous")
        started = time.perf_counter()
        raw = self.client.request(method, path, headers=headers, json=json_body, params=params)
        ms = int((time.perf_counter() - started) * 1000)
        resp = Response(raw, self.trace, who)
        self.total_calls += 1
        self.endpoints.add(endpoint_of(method, path))
        self.trace.calls.append(Call(who, method, path, raw.status_code, ms, resp.detail[:60]))
        return resp

    # thin verb helpers -- keep the scenarios readable
    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, **kw):
        return self.call("POST", path, **kw)

    def put(self, path, **kw):
        return self.call("PUT", path, **kw)

    def patch(self, path, **kw):
        return self.call("PATCH", path, **kw)

    def delete(self, path, **kw):
        return self.call("DELETE", path, **kw)


# =============================================================================
# Population model
# =============================================================================


@dataclass
class Actor:
    role: str
    name: str
    email: str
    token: str
    user_id: int | str | None = None
    password: str = PASSWORD

    @property
    def label(self) -> str:
        return f"{self.role}/{self.name.split()[0]}"


@dataclass
class Pupil:
    user_id: int
    name: str
    email: str
    class_id: int
    class_name: str
    parent_user_id: int | None = None
    parent: Actor | None = None
    login: Actor | None = None


@dataclass
class School:
    tenant_id: str
    display_name: str
    curriculum: str
    api: Api
    super_admin: Actor
    admin: Actor
    teachers: list[Actor] = field(default_factory=list)
    managers: list[Actor] = field(default_factory=list)
    levels: list[dict] = field(default_factory=list)
    classes: list[dict] = field(default_factory=list)
    pupils: list[Pupil] = field(default_factory=list)
    seed_event_id: int | None = None  # one published trip, provisioned once

    def head_teacher_of(self, class_id: int) -> Actor:
        klass = next(c for c in self.classes if c["id"] == class_id)
        lead = next(
            (t for t in self.teachers if int(t.user_id) == int(klass["head_teacher_id"] or -1)),
            None,
        )
        assert lead is not None, (
            f"section {klass['name']} (id {class_id}) is led by user "
            f"{klass['head_teacher_id']}, who is not one of this run's teachers -- "
            "provisioning assigns a head teacher to every section it creates"
        )
        return lead

    def pupils_in(self, class_id: int) -> list[Pupil]:
        return [p for p in self.pupils if p.class_id == class_id]

    def classes_with_families(self) -> list[dict]:
        """Sections holding at least one pupil who has both a parent and a login.

        Journeys must run in one of these, otherwise there is nobody to consent
        to the trip -- exactly as in a real school where an unregistered family
        cannot approve anything.
        """
        return [
            c for c in self.classes if any(p.parent and p.login for p in self.pupils_in(c["id"]))
        ]

    def family_in(self, class_id: int) -> Pupil:
        return next(p for p in self.pupils_in(class_id) if p.parent and p.login)

    def families(self) -> list[Pupil]:
        """Pupils who have both a parent account and their own login."""
        return [p for p in self.pupils if p.parent and p.login]


# =============================================================================
# Registration / login helpers
# =============================================================================


def register(
    api: Api, *, email: str, role: str, tenant: str | None, code: str, name: str = ""
) -> Actor:
    body = {"email": email, "password": PASSWORD, "role": role, "invite_code": code}
    if tenant:
        body["tenant_id"] = tenant
    if name:
        body["name"] = name
    resp = api.post("/api/v1/auth/register", json_body=body, label=f"signup/{role}").expect(200)
    actor = Actor(
        role=role,
        name=name or email.split(".")[0].title(),
        email=email,
        token=resp.json()["access_token"],
    )
    me = api.get("/api/v1/auth/me", actor=actor, tenant=tenant).expect(200).json()
    actor.user_id = me["user_id"]
    return actor


def login(api: Api, *, email: str, tenant: str, role: str, name: str) -> Actor:
    resp = api.post(
        "/api/v1/auth/login",
        json_body={"email": email, "password": PASSWORD, "tenant_id": tenant},
        label=f"login/{role}",
    ).expect(200)
    actor = Actor(role=role, name=name, email=email, token=resp.json()["access_token"])
    me = api.get("/api/v1/auth/me", actor=actor, tenant=tenant).expect(200).json()
    actor.user_id = me["user_id"]
    return actor


def structure_payload(
    rng: random.Random, curriculum: str, grade_names: list[str], grades: int, sections: int
) -> dict:
    levels = []
    for ordinal, grade in enumerate(grade_names[:grades], start=1):
        levels.append(
            {
                "name": grade,
                "isced_level": 1 if ordinal <= 3 else 2,
                "age_band_min": 4 + ordinal,
                "age_band_max": 5 + ordinal,
                "ordinal": ordinal,
                "is_active": True,
                "sections": [
                    {
                        "name": f"{grade} - {chr(ord('A') + i)}",
                        "capacity": rng.choice([20, 24, 25, 28]),
                    }
                    for i in range(sections)
                ],
            }
        )
    return {
        "system": curriculum,
        "levels": levels,
        "calendar": {
            "academic_year": "2026-2027",
            "start_month": 9,
            "weekend_days": ["Friday", "Saturday"],
        },
        "blackout_dates": [
            {"date": "2026-12-25", "title": "Winter break", "tags": ["holiday"]},
        ],
    }


def create_and_promote_admin(
    api: Api, operator: Actor, tenant: str, email: str, name: str
) -> Actor:
    """Create a school_admin without any invitation email.

    `AuthService.register_user` refuses to self-register a school_admin, and the
    invitation route needs a mailbox nobody can read in a test. So the operator
    creates an ordinary staff account (allowed even while the tenant is still in
    setup, because super_admin bypasses the tenant-live gate) and then promotes it
    with `PUT /auth/users/{id}/permissions` -- the same screen a real operator uses.
    """
    created = (
        api.post(
            "/api/v1/students/teachers",
            actor=operator,
            tenant=tenant,
            json_body={"email": email, "password": PASSWORD, "name": name},
        )
        .expect(200)
        .json()
    )
    api.put(
        f"/api/v1/auth/users/{created['id']}/permissions",
        actor=operator,
        tenant=tenant,
        json_body={"role": "school_admin", "roles": ["school_admin"], "permissions": []},
    ).expect(200)
    admin = login(api, email=email, tenant=tenant, role="school_admin", name=name)
    assert (
        "school_admin"
        in api.get("/api/v1/auth/me", actor=admin, tenant=tenant).expect(200).json()["roles"]
    ), "promotion to school_admin did not take effect"
    return admin


def onboard_school(
    api: Api, admin: Actor, tenant: str, name: str, rng: random.Random, structure: dict
) -> None:
    """Day-1 wizard, in the order the OnboardingWizardView walks the admin through."""
    api.put(
        "/api/v1/school/profile",
        actor=admin,
        tenant=tenant,
        json_body={
            "legal_name": f"{name} for General Education",
            "display_name": name,
            "school_code": f"QA{RUN_ID}",
            "country": "Jordan",
            "timezone": "Asia/Amman",
            "currency": "JOD",
        },
    ).expect(200)
    api.post(
        "/api/v1/school/campuses",
        actor=admin,
        tenant=tenant,
        json_body={
            "name": "Main Campus",
            "address_line1": rng.choice(STREETS),
            "city": rng.choice(CITIES),
            "country": "Jordan",
        },
    ).expect(200)
    api.post(
        "/api/v1/school/contacts",
        actor=admin,
        tenant=tenant,
        json_body={
            "role_title": "Principal",
            "name": f"Dr. {rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            "phone": f"+9627{rng.randint(10000000, 99999999)}",
            "is_emergency_contact": True,
            "escalation_order": 1,
        },
    ).expect(200)
    api.post("/api/v1/school/setup/commit-profile", actor=admin, tenant=tenant).expect(200)
    api.post(
        "/api/v1/students/structure/setup", actor=admin, tenant=tenant, json_body=structure
    ).expect(200)
    api.post("/api/v1/school/setup/activate", actor=admin, tenant=tenant).expect(200)


# =============================================================================
# Session fixtures -- one randomly generated school for the whole run
# =============================================================================


@pytest.fixture(scope="session")
def api() -> Api:
    if not ENABLED:
        pytest.skip("QA_E2E=0 -- live-stack journeys disabled")
    client = Api()
    try:
        probe = client.client.get("/api/v1/auth/tenants")
        if probe.status_code != 200:
            pytest.skip(
                f"API at {BASE_URL} answered {probe.status_code} -- start the stack (cd back && python run.py)"
            )
    except httpx.HTTPError as exc:
        pytest.skip(f"API at {BASE_URL} unreachable ({exc.__class__.__name__}) -- start the stack")
    yield client
    client.close()


@pytest.fixture(scope="session")
def school(api: Api) -> School:
    """Provision a whole random school: tenant, onboarding, staff, classes, families."""
    rng = random.Random(SEED)
    tenant = f"qa_{RUN_ID}"
    display = f"{rng.choice(SCHOOL_WORDS)} {rng.choice(SCHOOL_KINDS)}"
    curriculum, grade_names = rng.choice(CURRICULA)
    n_grades, n_sections = rng.randint(2, 3), rng.randint(2, 3)

    print(f"\n{'=' * 78}\nQA random journeys -- seed {SEED} (replay: QA_SEED={SEED})")
    print(f"  gateway    : {BASE_URL}")
    print(f"  school     : {display}  [{curriculum}, {n_grades} grades x {n_sections} sections]")
    print(f"  tenant     : {tenant}\n{'=' * 78}")

    # -- platform operator creates the school ------------------------------
    op_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    operator = register(
        api,
        email=email_for("ops", op_name),
        role="super_admin",
        tenant=None,
        code=SUPER_ADMIN_CODE,
        name=op_name,
    )
    api.post(
        "/api/v1/auth/tenants",
        actor=operator,
        json_body={"tenant_id": tenant, "name": display},
    ).expect(200)
    CREATED_TENANTS.append(tenant)

    # -- the operator staffs the school's first administrator ----------------
    # No invitation code is used anywhere in this suite: an invite is delivered
    # to a real mailbox. The account is created directly and then promoted, which
    # is the other route the app already supports.
    admin_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    admin_email = email_for("admin", admin_name)
    admin = create_and_promote_admin(api, operator, tenant, admin_email, admin_name)

    # -- day one --------------------------------------------------------------
    structure = structure_payload(rng, curriculum, grade_names, n_grades, n_sections)
    onboard_school(api, admin, tenant, display, rng, structure)

    sc = School(
        tenant_id=tenant,
        display_name=display,
        curriculum=curriculum,
        api=api,
        super_admin=operator,
        admin=admin,
    )

    # -- staff ----------------------------------------------------------------
    for _ in range(3):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        mail = email_for("teacher", name)
        api.post(
            "/api/v1/students/teachers",
            actor=admin,
            tenant=tenant,
            json_body={"email": mail, "password": PASSWORD, "name": name},
        ).expect(200)
        sc.teachers.append(login(api, email=mail, tenant=tenant, role="teacher", name=name))

    for _ in range(3):
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        mail = email_for("manager", name)
        api.post(
            "/api/v1/students/managers",
            actor=admin,
            tenant=tenant,
            json_body={"email": mail, "password": PASSWORD},
        ).expect(200)
        sc.managers.append(login(api, email=mail, tenant=tenant, role="manager", name=name))

    # -- classes: give every section a real head teacher ----------------------
    sc.levels = api.get("/api/v1/students/levels", actor=admin, tenant=tenant).expect(200).json()
    classes = api.get("/api/v1/students/classes", actor=admin, tenant=tenant).expect(200).json()
    for idx, klass in enumerate(classes):
        teacher = sc.teachers[idx % len(sc.teachers)]
        api.put(
            f"/api/v1/students/classes/{klass['id']}",
            actor=admin,
            tenant=tenant,
            json_body={"head_teacher_id": int(teacher.user_id)},
        ).expect(200)
    sc.classes = api.get("/api/v1/students/classes", actor=admin, tenant=tenant).expect(200).json()

    # -- pupils and their parents --------------------------------------------
    for klass in sc.classes:
        for _ in range(rng.randint(2, 4)):
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            mail = email_for("pupil", name)
            created = (
                api.post(
                    "/api/v1/students",
                    actor=admin,
                    tenant=tenant,
                    json_body={
                        "email": mail,
                        "password": PASSWORD,
                        "name": name,
                        "class_id": klass["id"],
                        "gender": rng.choice(["male", "female"]),
                        "birth_data": f"20{rng.randint(14, 19)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
                    },
                )
                .expect(200)
                .json()
            )
            pupil = Pupil(
                user_id=created["id"],
                name=name,
                email=mail,
                class_id=klass["id"],
                class_name=klass["name"],
            )
            # ~80% of pupils have a registered parent, like a real school roll
            if rng.random() < 0.8:
                p_name = f"{rng.choice(FIRST_NAMES)} {name.split()[-1]}"
                p_mail = email_for("parent", p_name)
                pupil.parent = register(
                    api,
                    email=p_mail,
                    role="parent",
                    tenant=tenant,
                    code=STAFF_CODE,
                    name=p_name,
                )
                parents = (
                    api.get("/api/v1/students/parents", actor=admin, tenant=tenant)
                    .expect(200)
                    .json()
                )
                pupil.parent_user_id = next(
                    p["id"] for p in parents if p["email"].lower() == p_mail.lower()
                )
                api.post(
                    "/api/v1/students/link-parent",
                    actor=admin,
                    tenant=tenant,
                    json_body={"student_id": pupil.user_id, "parent_id": pupil.parent_user_id},
                ).expect(200)
                pupil.login = login(api, email=mail, tenant=tenant, role="student", name=name)
            sc.pupils.append(pupil)

    # -- one already-published trip, so read-only cases have something to see --
    klass = rng.choice(sc.classes)
    teacher = sc.head_teacher_of(klass["id"])
    manager = rng.choice(sc.managers)
    event = create_draft(sc, teacher, [klass["id"]], rng)
    # Submit before pricing: a manager may not price a trip it cannot yet read
    # (F-03) -- the teacher has to hand it over first.
    submit(sc, teacher, event["id"])
    set_ticket_price(sc, manager, event["id"], rng.choice([7.5, 10.0, 12.5]))
    approve(sc, manager, event["id"])
    publish(sc, teacher, event["id"])
    sc.seed_event_id = event["id"]

    print(
        f"  provisioned: {len(sc.teachers)} teachers, {len(sc.managers)} managers, "
        f"{len(sc.classes)} classes, {len(sc.pupils)} pupils "
        f"({len(sc.families())} with a parent account) in {api.total_calls} calls"
    )
    yield sc
    print(f"\n{'=' * 78}")
    print(f"  HTTP calls this run : {api.total_calls}")
    print(f"  endpoints exercised : {len(api.endpoints)}")
    for endpoint in api.coverage():
        print(f"      {endpoint}")
    print(f"  tenants created     : {', '.join(CREATED_TENANTS)}")
    print(f"  replay this run     : QA_SEED={SEED}")
    print("=" * 78)


@pytest.fixture()
def rng(request) -> random.Random:
    """Per-case RNG, seeded from the run seed + case name: random, but replayable."""
    return random.Random(f"{SEED}:{request.node.nodeid}")


@pytest.fixture(autouse=True)
def _fresh_trace(api: Api):
    api.trace.reset()
    yield


# =============================================================================
# Domain actions -- the same calls the frontend makes, named after the UI
# =============================================================================


def create_draft(
    sc: School, teacher: Actor, class_ids: list[int], rng: random.Random, *, price: float = 0.0
) -> dict:
    """EventWizard steps 1-2: basics + audience."""
    day = rng.randint(1, 28)
    event = (
        sc.api.post(
            "/api/v1/events",
            actor=teacher,
            tenant=sc.tenant_id,
            json_body={
                "title": f"{rng.choice(TRIP_TITLES)} ({RUN_ID}-{next(_TRIP_SEQ)})",
                "description": "Curriculum-linked day trip organised by the class teacher.",
                "address": rng.choice(VENUES),
                "date": f"2026-1{rng.randint(0, 2)}-{day:02d}T08:30:00Z",
                "class_mappings": [{"class_id": cid, "ticket_price": price} for cid in class_ids],
            },
        )
        .expect(200)
        .json()
    )
    sc.api.post(
        f"/api/v1/events/{event['id']}/audience",
        actor=teacher,
        tenant=sc.tenant_id,
        json_body={"class_ids": class_ids},
    ).expect(200)
    return (
        sc.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )


def add_resources(sc: School, teacher: Actor, event_id: int, rng: random.Random) -> list[dict]:
    """EventWizard step 3: transport / supervisors / meals."""
    types = (
        sc.api.get("/api/v1/events/resource-types", actor=teacher, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )
    picks = rng.sample(types, k=min(len(types), rng.randint(2, 3)))
    lines = [
        {"resource_type_id": t["id"], "description": t["name"], "quantity": rng.randint(1, 4)}
        for t in picks
    ]
    sc.api.post(
        f"/api/v1/events/{event_id}/resources", actor=teacher, tenant=sc.tenant_id, json_body=lines
    ).expect(200)
    return lines


def price_resources(sc: School, manager: Actor, event_id: int, rng: random.Random) -> float:
    """Manager sets a unit price on every line; returns the expected total.

    (Costing used to belong to a separate "finance" role; that role was
    retired from the app and manager absorbed its duties.)
    """
    summary = (
        sc.api.get(f"/api/v1/events/{event_id}/resources", actor=manager, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )
    expected = 0.0
    for line in summary["resources"]:
        unit = float(rng.choice([15.0, 22.5, 40.0, 60.0]))
        sc.api.put(
            f"/api/v1/events/resources/{line['id']}/cost",
            actor=manager,
            tenant=sc.tenant_id,
            json_body={"unit_price": unit, "currency": "JOD"},
        ).expect(200)
        expected += unit * int(line["quantity"])
    return expected


def set_ticket_price(sc: School, pricer: Actor, event_id: int, price: float) -> None:
    """Manager sets the per-class ticket price.

    The class mappings are read as the school_admin on purpose: a manager
    cannot read -- or price -- an event while it is still a private draft
    (TenantService.check_event_permission / F-03), so `pricer` must be a
    manager acting on an event that has already been submitted, or school_admin.
    """
    detail = (
        sc.api.get(f"/api/v1/events/{event_id}", actor=sc.admin, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )
    sc.api.put(
        f"/api/v1/events/{event_id}/ticket-prices",
        actor=pricer,
        tenant=sc.tenant_id,
        json_body=[
            {"class_map_id": m["id"], "ticket_price": price} for m in detail["class_mappings"]
        ],
    ).expect(200)


def submit(sc: School, teacher: Actor, event_id: int) -> dict:
    return (
        sc.api.post(f"/api/v1/events/{event_id}/submit", actor=teacher, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )


def approve(sc: School, manager: Actor, event_id: int) -> dict:
    return (
        sc.api.post(
            f"/api/v1/events/{event_id}/manager-decision",
            actor=manager,
            tenant=sc.tenant_id,
            json_body={"decision": "approve"},
        )
        .expect(200)
        .json()
    )


def reject(sc: School, manager: Actor, event_id: int, reason: str) -> dict:
    return (
        sc.api.post(
            f"/api/v1/events/{event_id}/manager-decision",
            actor=manager,
            tenant=sc.tenant_id,
            json_body={"decision": "reject", "reason": reason},
        )
        .expect(200)
        .json()
    )


def publish(sc: School, actor: Actor, event_id: int) -> dict:
    return (
        sc.api.post(f"/api/v1/events/{event_id}/publish", actor=actor, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )


def class_map_id(sc: School, viewer: Actor, event_id: int, class_id: int) -> int:
    detail = (
        sc.api.get(f"/api/v1/events/{event_id}", actor=viewer, tenant=sc.tenant_id)
        .expect(200)
        .json()
    )
    return next(m["id"] for m in detail["class_mappings"] if int(m["class_id"]) == int(class_id))


def request_seat(sc: School, pupil: Pupil, cm_id: int) -> dict:
    return (
        sc.api.post(
            "/api/v1/students/enrollments",
            actor=pupil.login,
            tenant=sc.tenant_id,
            json_body={"student_id": pupil.user_id, "event_class_map_id": cm_id},
        )
        .expect(200)
        .json()
    )


def decide_enrollment(sc: School, actor: Actor, enrollment_id: int, state: str) -> dict:
    return (
        sc.api.post(
            f"/api/v1/students/enrollments/{enrollment_id}/approve",
            actor=actor,
            tenant=sc.tenant_id,
            json_body={"state": state},
        )
        .expect(200)
        .json()
    )


def pay(sc: School, parent: Actor, enrollment_id: int) -> dict:
    return (
        sc.api.post(
            f"/api/v1/events/enrollments/{enrollment_id}/pay", actor=parent, tenant=sc.tenant_id
        )
        .expect(200)
        .json()
    )


def payment_status(sc: School, actor: Actor, enrollment_id: int) -> dict:
    return (
        sc.api.get(
            f"/api/v1/events/enrollments/{enrollment_id}/payment", actor=actor, tenant=sc.tenant_id
        )
        .expect(200)
        .json()
    )


def full_trip_to_published(
    sc: School, rng: random.Random, *, class_ids: list[int] | None = None, ticket: float = 12.5
) -> dict:
    """The governance half of a journey: draft -> priced -> approved -> published."""
    class_ids = class_ids or [rng.choice(sc.classes_with_families())["id"]]
    teacher = sc.head_teacher_of(class_ids[0])
    manager = rng.choice(sc.managers)
    event = create_draft(sc, teacher, class_ids, rng)
    add_resources(sc, teacher, event["id"], rng)
    submit(sc, teacher, event["id"])
    price_resources(sc, manager, event["id"], rng)
    set_ticket_price(sc, manager, event["id"], ticket)
    approve(sc, manager, event["id"])
    published = publish(sc, teacher, event["id"])
    return {
        "event": published,
        "teacher": teacher,
        "manager": manager,
        "class_ids": class_ids,
    }


# =============================================================================
# KIND 1 -- DIRECT: one user, one action (25 cases)
# =============================================================================


@pytest.mark.qa_direct
class TestDirectActions:
    def test_d01_teacher_signs_in_and_sees_own_identity(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        fresh = login(
            school.api,
            email=teacher.email,
            tenant=school.tenant_id,
            role="teacher",
            name=teacher.name,
        )
        me = (
            school.api.get("/api/v1/auth/me", actor=fresh, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert me["email"].lower() == teacher.email.lower()
        assert me["tenant_id"] == school.tenant_id
        assert me["role"] == "teacher"

    def test_d02_wrong_password_is_refused(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        school.api.post(
            "/api/v1/auth/login",
            json_body={
                "email": teacher.email,
                "password": "definitely-not-it",
                "tenant_id": school.tenant_id,
            },
            label="login/attempt",
        ).expect(401, 400, because="a bad password must never mint a token")

    def test_d03_parent_opens_profile_and_sees_their_children(self, school: School, rng):
        pupil = rng.choice(school.families())
        profile = (
            school.api.get("/api/v1/auth/profile", actor=pupil.parent, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert profile["email"].lower() == pupil.parent.email.lower()
        names = [s["name"] for s in (profile.get("students") or [])]
        assert pupil.name in names, f"{pupil.name} missing from parent's children: {names}"

        linked = (
            school.api.get("/api/v1/students/linked", actor=pupil.parent, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert pupil.user_id in {child["id"] for child in linked}
        assert all(child["class_name"] for child in linked), "a child must show their section"

    def test_d04_student_profile_shows_class_and_parent(self, school: School, rng):
        pupil = rng.choice(school.families())
        profile = (
            school.api.get("/api/v1/auth/profile", actor=pupil.login, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert profile["class_id"] == pupil.class_id
        assert profile["class_name"] == pupil.class_name
        assert profile["parent_email"] is not None

    def test_d05_admin_opens_the_user_and_permission_matrix(self, school: School):
        rows = (
            school.api.get(
                "/api/v1/auth/users-permissions", actor=school.admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        roles = {r["role"] for r in rows}
        assert {"school_admin", "teacher", "manager"} <= roles, roles
        assert "finance" not in roles, "finance is retired -- manager owns pricing/cost now"
        assert len(rows) >= len(school.teachers) + len(school.managers) + 1

    def test_d06_admin_reads_the_roles_catalog(self, school: School):
        catalog = (
            school.api.get(
                "/api/v1/auth/roles-catalog", actor=school.admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        ids = {r["id"] for r in catalog["composite_roles"]}
        assert {"super_admin", "school_admin", "manager", "teacher", "parent", "student"} <= ids

    def test_d07_teacher_lists_the_classes_they_can_see(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        classes = (
            school.api.get("/api/v1/students/classes", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert len(classes) == len(school.classes)
        assert all(c["level_name"] for c in classes), "every section must show its grade"

    def test_d08_teacher_opens_their_class_roster(self, school: School, rng):
        klass = rng.choice([c for c in school.classes if school.pupils_in(c["id"])])
        teacher = school.head_teacher_of(klass["id"])
        roster = (
            school.api.get(
                f"/api/v1/students/classes/{klass['id']}/students",
                actor=teacher,
                tenant=school.tenant_id,
            )
            .expect(200)
            .json()
        )
        expected = {p.name for p in school.pupils_in(klass["id"])}
        assert {r["name"] for r in roster} == expected

    def test_d09_admin_lists_the_whole_student_roll(self, school: School):
        pupils = (
            school.api.get("/api/v1/students", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert len(pupils) == len(school.pupils)
        assert all(p["class_name"] for p in pupils), "every pupil must be placed in a section"

    def test_d10_admin_lists_teachers_and_parents(self, school: School):
        teachers = (
            school.api.get("/api/v1/students/teachers", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        parents = (
            school.api.get("/api/v1/students/parents", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert {t["email"].lower() for t in teachers} >= {t.email.lower() for t in school.teachers}
        assert len(parents) == len({p.parent.email for p in school.families()})

    def test_d11_admin_reads_the_academic_structure(self, school: School):
        structure = (
            school.api.get(
                "/api/v1/students/structure", actor=school.admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert structure["has_structure"] is True
        assert structure["system"] == school.curriculum
        assert structure["calendar"]["academic_year"] == "2026-2027"
        # AGENTS.md: grades sort naturally, never alphabetically
        ordinals = [lvl["ordinal"] for lvl in structure["levels"]]
        assert ordinals == sorted(ordinals)

    def test_d12_admin_reads_the_school_profile(self, school: School):
        profile = (
            school.api.get("/api/v1/school/profile", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert profile["display_name"] == school.display_name
        assert profile["currency"] == "JOD"

    def test_d13_setup_state_reports_the_school_as_live(self, school: School):
        state = (
            school.api.get(
                "/api/v1/school/setup-state", actor=school.admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert state["status"] == "live"
        assert state["blocking"] == []
        assert all(state["steps"].values())

    def test_d14_teacher_sees_the_seeded_resource_catalogue(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        types = (
            school.api.get("/api/v1/events/resource-types", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        categories = {t["category"] for t in types}
        assert len(types) >= 6, "the 6 system resource types must be seeded per tenant"
        assert {"transport", "staffing", "meals"} <= categories

    def test_d15_teacher_adds_a_custom_resource_type(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        name = f"Guided Tour {rng.randint(1000, 9999)}"
        created = (
            school.api.post(
                "/api/v1/events/resource-types",
                actor=teacher,
                tenant=school.tenant_id,
                json_body={"name": name, "category": "other"},
            )
            .expect(201)
            .json()
        )
        assert created["is_custom"] is True
        listed = (
            school.api.get("/api/v1/events/resource-types", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert name in {t["name"] for t in listed}

    def test_d16_teacher_starts_a_trip_draft(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        event = create_draft(school, teacher, [klass["id"]], rng)
        assert event["status"] == "draft"
        assert int(event["created_by"]) == int(teacher.user_id)
        assert [m["class_id"] for m in event["class_mappings"]] == [klass["id"]]

        mine = (
            school.api.get("/api/v1/events", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert event["id"] in {
            e["id"] for e in mine["events"]
        }, "the draft must appear on My Events"

    def test_d17_teacher_edits_their_own_draft(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        event = create_draft(school, teacher, [klass["id"]], rng)
        patched = (
            school.api.patch(
                f"/api/v1/events/{event['id']}",
                actor=teacher,
                tenant=school.tenant_id,
                json_body={
                    "description": "Updated after the parents' evening.",
                    "address": rng.choice(VENUES),
                },
            )
            .expect(200)
            .json()
        )
        assert patched["description"] == "Updated after the parents' evening."

    def test_d18_teacher_deletes_a_draft_they_abandoned(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        event = create_draft(school, teacher, [klass["id"]], rng)
        school.api.delete(
            f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id
        ).expect(200, 204)
        school.api.get(
            f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id
        ).expect(404, because="a deleted draft must be gone")

    def test_d19_manager_sets_the_school_subsidy_on_a_trip_under_review(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        submit(school, teacher, event["id"])  # a manager may not read a private draft

        amount = float(rng.choice([15, 20, 30]))
        school.api.put(
            f"/api/v1/events/{event['id']}/subsidy",
            actor=manager,
            tenant=school.tenant_id,
            json_body={"school_subsidy": amount},
        ).expect(200)
        detail = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=manager, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert float(detail["school_subsidy"]) == amount
        # ...and the same field is withheld from the teacher: events/router.py
        # blanks school_subsidy for anyone who is not manager or school_admin.
        as_teacher = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert as_teacher["school_subsidy"] is None

    def test_d20_manager_sets_a_ticket_price(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        submit(school, teacher, event["id"])  # a manager may not price a private draft
        price = float(rng.choice([5.0, 8.0, 14.0]))
        set_ticket_price(school, manager, event["id"], price)
        detail = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert [float(m["ticket_price"]) for m in detail["class_mappings"]] == [price]

    def test_d21_parent_opens_the_published_trips_list(self, school: School, rng):
        pupil = rng.choice(school.families())
        published = (
            school.api.get("/api/v1/events/published", actor=pupil.parent, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert isinstance(published, list)
        assert all(ev.get("class_mappings") is not None for ev in published)

    def test_d22_student_opens_the_published_trips_list(self, school: School, rng):
        pupil = rng.choice(school.families())
        published = (
            school.api.get("/api/v1/events/published", actor=pupil.login, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert isinstance(published, list)

    def test_d23_staff_member_updates_their_contact_details(self, school: School, rng):
        # Was xfailed (F-05): GET /auth/profile resolved a non-parent user through
        # get_user_by_email(), whose SELECT omitted phone/address, so a saved
        # number always read back empty. Both queries now select those columns.
        actor = rng.choice(school.teachers + school.managers)
        phone = f"+9627{rng.randint(10000000, 99999999)}"
        address = f"{rng.choice(STREETS)}, {rng.choice(CITIES)}"
        school.api.post(
            "/api/v1/auth/profile",
            actor=actor,
            tenant=school.tenant_id,
            json_body={"phone": phone, "address": address},
        ).expect(200)
        profile = (
            school.api.get("/api/v1/auth/profile", actor=actor, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert profile["phone"] == phone
        assert profile["address"] == address

    def test_d24_user_opens_their_notification_feed(self, school: School, rng):
        actor = rng.choice([p.parent for p in school.families()] + school.managers)
        feed = (
            school.api.get("/api/v1/notifications", actor=actor, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert isinstance(feed["notifications"], list)
        assert all("title" in n for n in feed["notifications"])

        unread = [n for n in feed["notifications"] if n["read_at"] is None]
        if unread:
            school.api.post(
                f"/api/v1/notifications/{unread[0]['id']}/read",
                actor=actor,
                tenant=school.tenant_id,
            ).expect(200)
            after = (
                school.api.get("/api/v1/notifications", actor=actor, tenant=school.tenant_id)
                .expect(200)
                .json()
            )
            cleared = next(n for n in after["notifications"] if n["id"] == unread[0]["id"])
            assert cleared["read_at"] is not None, "marking as read must stick"

    def test_d25_platform_operator_opens_the_analytics_dashboard(self, school: School):
        data = (
            school.api.get(
                "/api/v1/analytics/platform", actor=school.super_admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert data["platform_totals"]["total_tenants"] >= 1
        mine = next((t for t in data["tenants"] if t["tenant_id"] == school.tenant_id), None)
        assert (
            mine is not None
        ), "a school the operator just created must appear in platform analytics"
        assert mine["status"] == "success"
        assert mine["student_count"] == len(school.pupils)
        assert mine["class_count"] == len(school.classes)


# =============================================================================
# KIND 2 -- DEEP: multi-step, with the rules checked from outside (15 cases)
# =============================================================================


@pytest.mark.qa_deep
class TestDeepFlows:
    def test_p01_a_trip_with_no_audience_cannot_be_submitted(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        event = (
            school.api.post(
                "/api/v1/events",
                actor=teacher,
                tenant=school.tenant_id,
                json_body={
                    "title": f"Audience-less trip ({RUN_ID})",
                    "description": "No classes picked yet.",
                    "address": rng.choice(VENUES),
                    "date": "2026-11-05T09:00:00Z",
                    "class_mappings": [],
                },
            )
            .expect(200)
            .json()
        )
        school.api.post(
            f"/api/v1/events/{event['id']}/submit", actor=teacher, tenant=school.tenant_id
        ).expect(400, because="wizard step 2 is mandatory before submission")
        detail = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert detail["status"] == "draft", "a refused submission must not move the event"

    def test_p02_only_the_creator_may_submit_their_draft(self, school: School, rng):
        klass = rng.choice(school.classes)
        owner = school.head_teacher_of(klass["id"])
        other = next(t for t in school.teachers if t.email != owner.email)
        event = create_draft(school, owner, [klass["id"]], rng)

        school.api.post(
            f"/api/v1/events/{event['id']}/submit", actor=other, tenant=school.tenant_id
        ).expect(403, because="a colleague must not submit someone else's draft")
        # ...and the owner still can.
        assert submit(school, owner, event["id"])["status"] == "proposed"

    def test_p03_manager_rejection_needs_a_reason_and_hands_the_draft_back(
        self, school: School, rng
    ):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        submit(school, teacher, event["id"])

        school.api.post(
            f"/api/v1/events/{event['id']}/manager-decision",
            actor=manager,
            tenant=school.tenant_id,
            json_body={"decision": "reject", "reason": "   "},
        ).expect(400, because="a rejection with no reason is not actionable for the teacher")

        reason = rng.choice(REJECTION_REASONS)
        rejected = reject(school, manager, event["id"], reason)
        assert rejected["status"] == "draft"
        assert rejected["rejection_reason"] == reason
        feed = (
            school.api.get("/api/v1/notifications", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()["notifications"]
        )
        assert any(reason in (n["title"] or "") for n in feed), "the teacher must be told why"

    def test_p04_a_teacher_cannot_approve_their_own_proposal(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        event = create_draft(school, teacher, [klass["id"]], rng)
        submit(school, teacher, event["id"])
        school.api.post(
            f"/api/v1/events/{event['id']}/manager-decision",
            actor=teacher,
            tenant=school.tenant_id,
            json_body={"decision": "approve"},
        ).expect(403, because="approval is the manager's separation of duty")
        detail = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert detail["status"] == "proposed"

    def test_p05_manager_pricing_adds_up_into_the_event_total(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        add_resources(school, teacher, event["id"], rng)
        submit(school, teacher, event["id"])

        expected_total = price_resources(school, manager, event["id"], rng)
        summary = (
            school.api.get(
                f"/api/v1/events/{event['id']}/resources", actor=manager, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert summary["total_cost"] == pytest.approx(expected_total, rel=1e-6)
        for line in summary["resources"]:
            assert line["total_cost"] == pytest.approx(
                line["unit_price"] * line["quantity"], rel=1e-6
            )

    def test_p06_manager_sees_a_trip_only_once_it_leaves_the_teacher_s_desk(
        self, school: School, rng
    ):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        add_resources(school, teacher, event["id"], rng)

        # F-04 fixed this: get_event_resources used to leak its 403 out as a
        # 500 (raised inside its own try, re-wrapped by the trailing `except
        # Exception`). Now the correct status comes straight through.
        school.api.get(
            f"/api/v1/events/{event['id']}/resources",
            actor=manager,
            tenant=school.tenant_id,
        ).expect(403, because="a private draft must not be readable by a manager")
        submit(school, teacher, event["id"])
        summary = (
            school.api.get(
                f"/api/v1/events/{event['id']}/resources", actor=manager, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert summary["resources"], "after submission the manager must see the resource lines"

    def test_p07_predicted_attendance_is_eighty_percent_of_the_roll(self, school: School, rng):
        picked = rng.sample(school.classes, k=min(2, len(school.classes)))
        class_ids = [c["id"] for c in picked]
        teacher = school.head_teacher_of(class_ids[0])
        roll = sum(len(school.pupils_in(cid)) for cid in class_ids)
        expected = round(0.8 * roll)

        event = create_draft(school, teacher, class_ids, rng)
        prediction = (
            school.api.get(
                f"/api/v1/events/{event['id']}/audience/prediction",
                actor=teacher,
                tenant=school.tenant_id,
                params={"class_ids": ",".join(str(cid) for cid in class_ids)},
            )
            .expect(200)
            .json()
        )
        assert prediction["predicted_attendance"] == expected
        submitted = submit(school, teacher, event["id"])
        assert submitted["predicted_attendance"] == expected

    def test_p08_only_managers_and_admins_may_touch_the_money(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        # A teacher asking for a subsidy is silently zeroed, not trusted.
        event = (
            school.api.post(
                "/api/v1/events",
                actor=teacher,
                tenant=school.tenant_id,
                json_body={
                    "title": f"Subsidy probe ({RUN_ID})",
                    "description": "Teacher tries to set the school subsidy.",
                    "address": rng.choice(VENUES),
                    "school_subsidy": 250.0,
                    "date": "2026-11-12T09:00:00Z",
                    "class_mappings": [{"class_id": klass["id"], "ticket_price": 0.0}],
                },
            )
            .expect(200)
            .json()
        )
        assert (
            float(event["school_subsidy"] or 0) == 0.0
        ), "teachers must not be able to grant subsidy"

        cm_id = class_map_id(school, teacher, event["id"], klass["id"])
        school.api.put(
            f"/api/v1/events/{event['id']}/ticket-prices",
            actor=teacher,
            tenant=school.tenant_id,
            json_body=[{"class_map_id": cm_id, "ticket_price": 99.0}],
        ).expect(403, because="ticket pricing belongs to manager/admin")

        submit(school, teacher, event["id"])  # a manager may not price a private draft either
        set_ticket_price(school, manager, event["id"], 11.0)
        detail = (
            school.api.get(f"/api/v1/events/{event['id']}", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert float(detail["class_mappings"][0]["ticket_price"]) == 11.0

    def test_p09_the_curriculum_locks_at_activation_but_sections_stay_editable(
        self, school: School, rng
    ):
        other_system = "International" if school.curriculum == "UK" else "UK"
        current = (
            school.api.get(
                "/api/v1/students/structure", actor=school.admin, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        payload = {
            "system": other_system,
            "levels": [
                {
                    "name": lvl["name"],
                    "isced_level": lvl.get("isced_level"),
                    "age_band_min": lvl.get("age_band_min"),
                    "age_band_max": lvl.get("age_band_max"),
                    "ordinal": lvl.get("ordinal"),
                    "is_active": True,
                    "sections": [
                        {"name": s["name"], "capacity": s.get("capacity") or 25}
                        for s in lvl["sections"]
                    ],
                }
                for lvl in current["levels"]
            ],
            "calendar": current["calendar"],
            "blackout_dates": [],
        }
        school.api.post(
            "/api/v1/students/structure/setup",
            actor=school.admin,
            tenant=school.tenant_id,
            json_body=payload,
        ).expect(403, because="the curriculum system is locked once the school goes live")

        # Same system, one extra section: allowed.
        payload["system"] = school.curriculum
        grade = payload["levels"][0]
        new_section = f"{grade['name']} - {chr(ord('A') + len(grade['sections']))}"
        grade["sections"].append({"name": new_section, "capacity": 25})
        school.api.post(
            "/api/v1/students/structure/setup",
            actor=school.admin,
            tenant=school.tenant_id,
            json_body=payload,
        ).expect(200)
        after = (
            school.api.get("/api/v1/students/classes", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert new_section in {c["name"] for c in after}
        # Deliberately NOT written back into school.classes: the new section has no
        # head teacher and no pupils, and the other 49 cases share that list.

    def test_p10_the_enrollment_chain_must_run_in_order(self, school: School, rng):
        trip = full_trip_to_published(school, rng)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)

        enrollment = request_seat(school, pupil, cm_id)
        assert enrollment["state"] == "requested_by_student"

        school.api.post(
            f"/api/v1/students/enrollments/{enrollment['id']}/approve",
            actor=trip["teacher"],
            tenant=school.tenant_id,
            json_body={"state": "approved_by_teacher"},
        ).expect(400, because="the parent consents before the trip lead confirms the seat")

        assert (
            decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")["state"]
            == "approved_by_parent"
        )
        school.api.post(
            f"/api/v1/students/enrollments/{enrollment['id']}/approve",
            actor=pupil.parent,
            tenant=school.tenant_id,
            json_body={"state": "approved_by_parent"},
        ).expect(400, because="a parent cannot consent twice")
        assert (
            decide_enrollment(school, trip["teacher"], enrollment["id"], "approved_by_teacher")[
                "state"
            ]
            == "approved_by_teacher"
        )

    def test_p11_a_seat_request_is_validated_against_the_roster(self, school: School, rng):
        trip = full_trip_to_published(school, rng)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)
        request_seat(school, pupil, cm_id)

        # A pupil from another section cannot take a seat on this class mapping.
        outsider = next(
            (p for p in school.pupils if p.class_id != klass_id and p.login),
            None,
        )
        if outsider:
            school.api.post(
                "/api/v1/students/enrollments",
                actor=outsider.login,
                tenant=school.tenant_id,
                json_body={"student_id": outsider.user_id, "event_class_map_id": cm_id},
            ).expect(400, because="the pupil is not in the class this trip targets")

        # An unknown pupil id is rejected outright.
        school.api.post(
            "/api/v1/students/enrollments",
            actor=pupil.login,
            tenant=school.tenant_id,
            json_body={"student_id": 999_999, "event_class_map_id": cm_id},
        ).expect(400, 404)

    def test_p12_payment_is_the_parent_s_job_and_only_when_there_is_a_price(
        self, school: School, rng
    ):
        # Priced trip: a pending payment exists and only the parent may settle it.
        priced = full_trip_to_published(school, rng, ticket=float(rng.choice([9.5, 12.0, 18.0])))
        klass_id = priced["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, priced["teacher"], priced["event"]["id"], klass_id)
        enrollment = request_seat(school, pupil, cm_id)
        decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")

        before = payment_status(school, pupil.parent, enrollment["id"])
        assert before["status"] == "pending"
        assert before["amount"] == pytest.approx(float(enrollment["ticket_price"]), rel=1e-6)

        school.api.post(
            f"/api/v1/events/enrollments/{enrollment['id']}/pay",
            actor=pupil.login,
            tenant=school.tenant_id,
        ).expect(403, because="pupils must not be able to pay their own fees")
        pay(school, pupil.parent, enrollment["id"])
        assert payment_status(school, pupil.parent, enrollment["id"])["status"] == "paid"

        # Free trip: no payment record is created at all.
        free = full_trip_to_published(school, rng, ticket=0.0)
        free_class = free["class_ids"][0]
        free_pupil = school.family_in(free_class)
        free_cm = class_map_id(school, free["teacher"], free["event"]["id"], free_class)
        free_enrollment = request_seat(school, free_pupil, free_cm)
        school.api.get(
            f"/api/v1/events/enrollments/{free_enrollment['id']}/payment",
            actor=free_pupil.parent,
            tenant=school.tenant_id,
        ).expect(404, because="a free trip must not raise an invoice")

    def test_p13_health_records_are_stored_encrypted_and_read_back_masked(
        self, school: School, rng
    ):
        pupil = rng.choice(school.pupils)
        national_id = f"99{rng.randint(10000000, 99999999)}"
        emergency = f"+9627{rng.randint(10000000, 99999999)}"
        school.api.post(
            f"/api/v1/students/{pupil.user_id}/health",
            actor=school.admin,
            tenant=school.tenant_id,
            json_body={
                "national_id": national_id,
                "medical_conditions": "Mild asthma, carries an inhaler",
                "emergency_contact": emergency,
            },
        ).expect(200)

        record = (
            school.api.get(
                f"/api/v1/students/{pupil.user_id}/health",
                actor=school.admin,
                tenant=school.tenant_id,
            )
            .expect(200)
            .json()
        )
        assert record["is_masked"] is True
        assert record["national_id"] != national_id
        assert record["national_id"].endswith(national_id[-4:])
        assert "*" in record["emergency_contact"]

        school.api.get(f"/api/v1/students/{pupil.user_id}/health", label="anonymous").expect(
            401, 403, because="PII must never be readable without a token"
        )

    def test_p14_a_tenant_header_cannot_be_used_to_read_another_school(self, school: School, rng):
        teacher = rng.choice(school.teachers)
        mine = {
            p["email"].lower()
            for p in school.api.get("/api/v1/students", actor=school.admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        }

        # The SPA lets a user pick a tenant; the backend must ignore it for
        # everyone except a platform operator.
        spoofed = school.api.get(
            "/api/v1/students", actor=teacher, tenant="tenant_a", label="teacher/spoofing"
        ).expect(200, 403)
        if spoofed.status == 200:
            seen = {p["email"].lower() for p in spoofed.json()}
            assert (
                seen <= mine
            ), "a teacher sent X-Tenant-ID: tenant_a and got another school's pupils"

        # The operator, by design, can switch schools.
        as_operator = (
            school.api.get("/api/v1/students", actor=school.super_admin, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert {p["email"].lower() for p in as_operator} == mine

    def test_p15_the_retired_finance_surfaces_are_gone_not_just_inert(self, school: School, rng):
        """finance_approval / final_review were enum values with no reachable
        transition even before the finance role existed (PROJECT_UNDERSTANDING.md
        sec.9) -- dead, but still callable. Now that finance is retired, the
        endpoints that only made sense for that dead flow (finance-queue,
        finance-submit) are removed outright rather than left as permanently-
        empty/400-ing dead code. Pinned so a future change has to update this
        test deliberately."""
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        add_resources(school, teacher, event["id"], rng)
        submit(school, teacher, event["id"])

        # 422, not 404: with the route gone, `GET /api/v1/events/finance-queue`
        # now falls through to `GET /api/v1/events/{event_id}`, which rejects
        # "finance-queue" as a non-integer event id. Either way it is no longer
        # a working endpoint.
        school.api.get(
            "/api/v1/events/finance-queue", actor=manager, tenant=school.tenant_id
        ).expect(404, 422, because="the finance queue endpoint was removed with the finance role")
        school.api.post(
            f"/api/v1/events/{event['id']}/finance-submit", actor=manager, tenant=school.tenant_id
        ).expect(404, because="finance-submit was removed with the finance role")
        school.api.post(
            f"/api/v1/events/{event['id']}/final-decision",
            actor=manager,
            tenant=school.tenant_id,
            json_body={"decision": "publish"},
        ).expect(404, because="final-decision was removed with the finance role")

        # The reachable queue, by contrast, must contain this proposal.
        manager_queue = (
            school.api.get(
                "/api/v1/events/manager-queue",
                actor=rng.choice(school.managers),
                tenant=school.tenant_id,
            )
            .expect(200)
            .json()
        )
        assert event["id"] in {e["id"] for e in manager_queue["events"]}


# =============================================================================
# KIND 3 -- FULL: complete journeys, several users, end to end (10 cases)
# =============================================================================


@pytest.mark.qa_full
class TestFullProcesses:
    def test_f01_a_trip_runs_from_first_draft_to_a_paid_seat(self, school: School, rng):
        klass = rng.choice(school.classes_with_families())
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        ticket = float(rng.choice([8.0, 12.5, 15.0]))

        # 1. teacher plans the trip in the wizard
        event = create_draft(school, teacher, [klass["id"]], rng)
        add_resources(school, teacher, event["id"], rng)
        assert event["status"] == "draft"

        # 2. teacher submits; managers are notified
        assert submit(school, teacher, event["id"])["status"] == "proposed"
        mgr_feed = (
            school.api.get("/api/v1/notifications", actor=manager, tenant=school.tenant_id)
            .expect(200)
            .json()["notifications"]
        )
        assert any(event["title"] in (n["title"] or "") for n in mgr_feed)

        # 3. manager costs it, prices the ticket, and approves
        expected_cost = price_resources(school, manager, event["id"], rng)
        set_ticket_price(school, manager, event["id"], ticket)
        approved = approve(school, manager, event["id"])
        assert approved["status"] == "approved"
        assert approved["manager_approved_at"] is not None

        # 4. teacher publishes; the class and their parents hear about it
        published = publish(school, teacher, event["id"])
        assert published["status"] == "published"
        assert published["published_at"] is not None

        pupil = school.family_in(klass["id"])
        seen_by_pupil = (
            school.api.get("/api/v1/events/published", actor=pupil.login, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert event["id"] in {e["id"] for e in seen_by_pupil}
        parent_feed = (
            school.api.get("/api/v1/notifications", actor=pupil.parent, tenant=school.tenant_id)
            .expect(200)
            .json()["notifications"]
        )
        assert parent_feed, "parents of a targeted class must be notified on publish"

        # 5. pupil asks for a seat, parent consents and pays, trip lead confirms
        cm_id = class_map_id(school, teacher, event["id"], klass["id"])
        enrollment = request_seat(school, pupil, cm_id)
        decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")
        invoice = payment_status(school, pupil.parent, enrollment["id"])
        assert invoice["amount"] == pytest.approx(ticket, rel=1e-6)
        pay(school, pupil.parent, enrollment["id"])
        assert payment_status(school, pupil.parent, enrollment["id"])["status"] == "paid"
        final = decide_enrollment(school, teacher, enrollment["id"], "approved_by_teacher")
        assert final["state"] == "approved_by_teacher"

        # 6. the trip lead sees the seat on the roster, the parent leaves feedback
        roster = (
            school.api.get("/api/v1/students/enrollments", actor=teacher, tenant=school.tenant_id)
            .expect(200)
            .json()
        )
        assert enrollment["id"] in {e["id"] for e in roster}
        school.api.post(
            f"/api/v1/events/{event['id']}/feedbacks",
            actor=pupil.parent,
            tenant=school.tenant_id,
            json_body={"rating": rng.randint(4, 5), "comments": rng.choice(FEEDBACK_NOTES)},
        ).expect(200)
        feedback = (
            school.api.get(
                f"/api/v1/events/{event['id']}/feedbacks", actor=teacher, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert len(feedback) == 1
        # and the priced total survived the whole journey
        summary = (
            school.api.get(
                f"/api/v1/events/{event['id']}/resources", actor=manager, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert summary["total_cost"] == pytest.approx(expected_cost, rel=1e-6)

    def test_f02_a_rejected_trip_is_reworked_and_approved_on_the_second_pass(
        self, school: School, rng
    ):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)

        event = create_draft(school, teacher, [klass["id"]], rng)
        add_resources(school, teacher, event["id"], rng)
        submit(school, teacher, event["id"])

        reason = rng.choice(REJECTION_REASONS)
        assert reject(school, manager, event["id"], reason)["status"] == "draft"

        # The teacher can edit again only because it came back to draft.
        school.api.patch(
            f"/api/v1/events/{event['id']}",
            actor=teacher,
            tenant=school.tenant_id,
            json_body={"description": f"Revised after review: {reason}"},
        ).expect(200)
        add_resources(school, teacher, event["id"], rng)
        submit(school, teacher, event["id"])
        price_resources(school, manager, event["id"], rng)
        set_ticket_price(school, manager, event["id"], 9.0)
        second = approve(school, manager, event["id"])
        assert second["status"] == "approved"
        assert second["rejection_reason"] in (None, "")
        assert publish(school, teacher, event["id"])["status"] == "published"

    def test_f03_a_manager_publishes_on_the_teacher_s_behalf(self, school: School, rng):
        klass = rng.choice(school.classes)
        teacher = school.head_teacher_of(klass["id"])
        manager = rng.choice(school.managers)
        event = create_draft(school, teacher, [klass["id"]], rng)
        submit(school, teacher, event["id"])
        approve(school, manager, event["id"])

        published = publish(school, manager, event["id"])
        assert published["status"] == "published"
        assert (
            published["published_at"] is not None
        ), "the manager override must still stamp published_at"
        pupils = [p for p in school.pupils_in(klass["id"]) if p.login]
        if pupils:
            visible = (
                school.api.get(
                    "/api/v1/events/published", actor=pupils[0].login, tenant=school.tenant_id
                )
                .expect(200)
                .json()
            )
            assert event["id"] in {e["id"] for e in visible}

    def test_f04_a_free_trip_needs_consent_but_no_money(self, school: School, rng):
        trip = full_trip_to_published(school, rng, ticket=0.0)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)

        enrollment = request_seat(school, pupil, cm_id)
        decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")
        confirmed = decide_enrollment(
            school, trip["teacher"], enrollment["id"], "approved_by_teacher"
        )
        assert confirmed["state"] == "approved_by_teacher"
        assert float(confirmed["ticket_price"] or 0) == 0.0
        school.api.get(
            f"/api/v1/events/enrollments/{enrollment['id']}/payment",
            actor=pupil.parent,
            tenant=school.tenant_id,
        ).expect(404, because="nothing to pay on a free trip")

    def test_f05_a_parent_declines_and_the_seat_never_becomes_payable(self, school: School, rng):
        trip = full_trip_to_published(school, rng, ticket=14.0)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)

        enrollment = request_seat(school, pupil, cm_id)
        declined = decide_enrollment(school, pupil.parent, enrollment["id"], "rejected_by_parent")
        assert declined["state"] == "rejected_by_parent"
        school.api.post(
            f"/api/v1/students/enrollments/{enrollment['id']}/approve",
            actor=trip["teacher"],
            tenant=school.tenant_id,
            json_body={"state": "approved_by_teacher"},
        ).expect(400, because="a declined seat cannot be confirmed by the trip lead")
        assert payment_status(school, pupil.parent, enrollment["id"])["status"] == "pending"

    def test_f06_the_trip_lead_can_refuse_a_seat_after_parental_consent(self, school: School, rng):
        trip = full_trip_to_published(school, rng, ticket=10.0)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)

        enrollment = request_seat(school, pupil, cm_id)
        decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")
        refused = decide_enrollment(
            school, trip["teacher"], enrollment["id"], "rejected_by_teacher"
        )
        assert refused["state"] == "rejected_by_teacher"
        mine = (
            school.api.get(
                "/api/v1/students/enrollments", actor=pupil.parent, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert (
            next(e["state"] for e in mine if e["id"] == enrollment["id"]) == "rejected_by_teacher"
        )

    def test_f07_a_whole_year_group_travels_on_one_trip(self, school: School, rng):
        class_ids = [c["id"] for c in school.classes_with_families()[:3]]
        trip = full_trip_to_published(school, rng, class_ids=class_ids, ticket=6.5)
        teacher = trip["teacher"]

        detail = (
            school.api.get(
                f"/api/v1/events/{trip['event']['id']}", actor=teacher, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert {m["class_id"] for m in detail["class_mappings"]} == set(class_ids)
        expected_roll = sum(len(school.pupils_in(cid)) for cid in class_ids)
        assert detail["predicted_attendance"] == round(0.8 * expected_roll)

        booked: dict[int, set[int]] = {}
        for cid in class_ids:
            cm_id = class_map_id(school, teacher, trip["event"]["id"], cid)
            for pupil in [p for p in school.pupils_in(cid) if p.parent and p.login][:2]:
                enrollment = request_seat(school, pupil, cm_id)
                decide_enrollment(school, pupil.parent, enrollment["id"], "approved_by_parent")
                pay(school, pupil.parent, enrollment["id"])
                decide_enrollment(school, teacher, enrollment["id"], "approved_by_teacher")
                booked.setdefault(cid, set()).add(enrollment["id"])
        assert booked, "the year group must contain at least one registered family"

        # Each section's own head teacher sees that section's seats: a roster is
        # scoped to the class you lead, not to the whole trip.
        for cid, seat_ids in booked.items():
            lead = school.head_teacher_of(cid)
            roster = (
                school.api.get("/api/v1/students/enrollments", actor=lead, tenant=school.tenant_id)
                .expect(200)
                .json()
            )
            seats = [e for e in roster if e["id"] in seat_ids]
            assert len(seats) == len(
                seat_ids
            ), f"the lead of {cid} should see every seat they confirmed for this trip"
            assert all(e["state"] == "approved_by_teacher" for e in seats)

    def test_f08_last_term_s_trip_is_cloned_and_run_again(self, school: School, rng):
        first = full_trip_to_published(school, rng, ticket=11.0)
        teacher, manager = first["teacher"], first["manager"]

        clone = (
            school.api.post(
                f"/api/v1/events/{first['event']['id']}/clone",
                actor=teacher,
                tenant=school.tenant_id,
            )
            .expect(200)
            .json()
        )
        assert clone["status"] == "draft"
        assert clone["id"] != first["event"]["id"]
        assert first["event"]["title"] in clone["title"]

        school.api.post(
            f"/api/v1/events/{clone['id']}/audience",
            actor=teacher,
            tenant=school.tenant_id,
            json_body={"class_ids": first["class_ids"]},
        ).expect(200)
        submit(school, teacher, clone["id"])
        set_ticket_price(school, manager, clone["id"], 11.0)
        approve(school, manager, clone["id"])
        assert publish(school, teacher, clone["id"])["status"] == "published"

    def test_f09_a_brand_new_school_is_locked_until_it_finishes_day_one(self, school: School, rng):
        """The whole onboarding gate, on its own fresh tenant."""
        tenant = f"qa_{RUN_ID}_new"
        display = f"{rng.choice(SCHOOL_WORDS)} {rng.choice(SCHOOL_KINDS)}"
        api = school.api
        api.post(
            "/api/v1/auth/tenants",
            actor=school.super_admin,
            json_body={"tenant_id": tenant, "name": display},
        ).expect(200)
        CREATED_TENANTS.append(tenant)

        # The operator creates the school's first administrator directly and
        # promotes the account -- no invitation email is involved anywhere.
        admin_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        admin_email = email_for("admin", admin_name, tenant_suffix=".new")
        admin = create_and_promote_admin(api, school.super_admin, tenant, admin_email, admin_name)
        # The new admin can sign in straight away, before any setup is done.
        login(api, email=admin_email, tenant=tenant, role="school_admin", name=admin_name)

        state = api.get("/api/v1/school/setup-state", actor=admin, tenant=tenant).expect(200).json()
        assert state["status"] == "setup"
        assert state["blocking"], "a fresh school must be told what is missing"
        api.get("/api/v1/students", actor=admin, tenant=tenant).expect(
            403, because="no tenant-scoped work is allowed before activation"
        )
        api.post("/api/v1/school/setup/activate", actor=admin, tenant=tenant).expect(
            400, because="activation cannot be skipped ahead of the two stages"
        )

        curriculum, grades = rng.choice(CURRICULA)
        onboard_school(
            api, admin, tenant, display, rng, structure_payload(rng, curriculum, grades, 2, 2)
        )
        assert (
            api.get("/api/v1/school/setup-state", actor=admin, tenant=tenant)
            .expect(200)
            .json()["status"]
            == "live"
        )

        # And now the school can actually be run: staff, roster, first trip.
        t_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        t_email = email_for("teacher", t_name, tenant_suffix=".new")
        api.post(
            "/api/v1/students/teachers",
            actor=admin,
            tenant=tenant,
            json_body={"email": t_email, "password": PASSWORD, "name": t_name},
        ).expect(200)
        teacher = login(api, email=t_email, tenant=tenant, role="teacher", name=t_name)
        classes = api.get("/api/v1/students/classes", actor=admin, tenant=tenant).expect(200).json()
        assert classes, "the curriculum wizard must have created the sections"

        p_name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        api.post(
            "/api/v1/students",
            actor=admin,
            tenant=tenant,
            json_body={
                "email": email_for("pupil", p_name, tenant_suffix=".new"),
                "password": PASSWORD,
                "name": p_name,
                "class_id": classes[0]["id"],
                "gender": "female",
                "birth_data": "2017-05-09",
            },
        ).expect(200)
        first_trip = (
            api.post(
                "/api/v1/events",
                actor=teacher,
                tenant=tenant,
                json_body={
                    "title": f"Welcome-week visit ({RUN_ID})",
                    "description": "First trip of the new school.",
                    "address": rng.choice(VENUES),
                    "date": "2026-10-08T09:00:00Z",
                    "class_mappings": [{"class_id": classes[0]["id"], "ticket_price": 0.0}],
                },
            )
            .expect(200)
            .json()
        )
        assert first_trip["status"] == "draft"

    def test_f10_a_family_cancels_a_seat_and_books_it_again(self, school: School, rng):
        trip = full_trip_to_published(school, rng, ticket=13.0)
        klass_id = trip["class_ids"][0]
        pupil = school.family_in(klass_id)
        cm_id = class_map_id(school, trip["teacher"], trip["event"]["id"], klass_id)

        first = request_seat(school, pupil, cm_id)
        decide_enrollment(school, pupil.parent, first["id"], "approved_by_parent")
        school.api.delete(
            f"/api/v1/students/enrollments/{first['id']}",
            actor=pupil.parent,
            tenant=school.tenant_id,
        ).expect(200, 204)
        remaining = (
            school.api.get(
                "/api/v1/students/enrollments", actor=pupil.parent, tenant=school.tenant_id
            )
            .expect(200)
            .json()
        )
        assert first["id"] not in {e["id"] for e in remaining}

        again = request_seat(school, pupil, cm_id)
        assert again["id"] != first["id"]
        decide_enrollment(school, pupil.parent, again["id"], "approved_by_parent")
        pay(school, pupil.parent, again["id"])
        assert payment_status(school, pupil.parent, again["id"])["status"] == "paid"
        assert (
            decide_enrollment(school, trip["teacher"], again["id"], "approved_by_teacher")["state"]
            == "approved_by_teacher"
        )
