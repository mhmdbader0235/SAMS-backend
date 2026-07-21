---
trigger: always_on
---

there part 1 before this file in same folder


## 6. Pydantic Schemas (sketch — expand field validators as needed)

```python
class EventCreate(BaseModel):
    title: str
    description: str
    address: str
    date: datetime

class AudienceSelect(BaseModel):
    class_ids: list[int]

class ResourceLineIn(BaseModel):
    resource_type_id: int
    description: str | None = None
    quantity: int = Field(gt=0)

class ResourceCostIn(BaseModel):
    unit_price: Decimal = Field(ge=0)
    currency: str = "JOD"

class ManagerDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = None

class PublishedEventOut(BaseModel):
    title: str
    description: str
    address: str
    date: datetime
    total_cost: Decimal
```

---

## 7. Vue Frontend

### 7.1 Teacher wizard — component tree
```
EventWizard.vue                 (holds current step + shared event draft state)
├── StepBasics.vue               title/description/address/date form
├── StepAudience.vue              class multi-select + predicted attendance display
├── StepResources.vue             checkbox grid (grouped by category) + qty inputs + "add custom type" modal
├── StepProposalReview.vue        read-only summary of all above + Save Draft / Send for Approval buttons
└── WizardStepper.vue             progress indicator, shared by all steps
```
Since you're still building Vue fundamentals: keep the draft event object in
a single reactive store (Pinia, if you've reached that in your learning path)
so each step component just reads/writes slices of it, rather than passing
props down four levels and emitting events back up.

### 7.2 Manager dashboard
```
ManagerDashboard.vue
├── ProposedQueue.vue      list of events in `proposed`, approve/reject actions
└── FinalReviewQueue.vue   list of events in `final_review`, publish/return actions
```

### 7.3 Finance dashboard
```
FinanceDashboard.vue
└── PricingQueue.vue       list of events in `finance_approval`
    └── ResourcePricingTable.vue   per-resource unit_price input, running total, "Send to Manager" button
```

### 7.4 Parent/Student view
```
PublishedEventCard.vue   title, description, address, date, total_cost only
```

---

## 8. Task List (execute in order; ⏸ = stop for review before continuing)

1. Write Alembic migration: create `resource_types`, `resources`, `resource_cost`; alter `events` with new columns and the `event_status` enum. Do not drop `cost_budget` yet.
2. Data-migrate the seed `resource_types` rows in the same migration.
3. Add `manager` and `finance` roles via existing role seeding mechanism. ⏸
4. Build repository classes for `resource_types`, `resources`, `resource_cost` following your existing repository pattern.
5. Build service-layer functions: `create_resource_type`, `add_resources_to_event`, `set_resource_cost`, `get_resource_summary(event_id)` (returns lines + total).
6. Implement `transition_event()` as the single state-machine function described in section 3, with the transition table as data.
7. Implement `check_event_permission()` as the single permission-check function described in section 4. ⏸
8. Build API endpoints from section 5, wiring each one through `check_event_permission` and, where relevant, `transition_event`.
9. Write the attendance-prediction calculation (decide the formula per the open question in section 3/original spec, then implement as a single service function, not inline in the router).
10. Write tests for the state machine: every legal transition succeeds, every illegal one (wrong role, wrong current state) is rejected. ⏸
11. Scaffold `EventWizard.vue` and the four step components with a shared store for the in-progress event.
12. Wire `StepResources.vue` to `GET /resource-types` and the "add custom type" flow to `POST /resource-types`.
13. Wire `StepProposalReview.vue`'s two buttons to `save_draft` (no-op if already draft) and `submit_for_approval`.
14. Build `ManagerDashboard.vue` and its two queues, wired to `manager-decision` / `final-decision` endpoints.
15. Build `FinanceDashboard.vue` and `ResourcePricingTable.vue`, wired to `PUT /resources/{id}/cost` and `finance-submit`.
16. Build `PublishedEventCard.vue` and the published-events list for parents/students.
17. Once everything above is verified working end to end, write a follow-up migration to drop `cost_budget`. ⏸

---

## 9. Decisions (resolved)
- Reject/return reason: **required** — enforced as a non-empty field in `ManagerDecision`/the return-to-finance schema, not just a frontend prompt.
- Finance **can** edit `resources` rows (quantity/type), not only `resource_cost` — see section 2.2/3/4.
- Teacher-added custom resource types are available to the whole school automatically, since `resource_types` lives inside that tenant's own database — no `tenant_id` scoping column needed. See section 2.1.
- Attendance prediction formula: **80% of the total count of students across the classes selected for the event** (i.e. `0.8 * sum(enrollment count per selected class)`), not a historical-attendance-based estimate. Implement as a single service function per section 3/task 9 — it only needs `ENROLLMENTS`/`CLASSES`, not `ATTENDANCE`.
- Teacher edit access: **read-only from the moment they submit** (`draft` → `proposed`) for the rest of the event's lifecycle, per section 4 — they only regain write access if the event returns to `draft`, and even then only because of the status, not a standing owner privilege.
- `POST /events/{id}/resources` semantics: **full replace**, not upsert. Each call deletes the event's existing `resources` rows (only reachable while `draft`, so nothing finance touched can be in scope) and inserts the array sent in the request. This matches how a single-page-per-step wizard naturally works — the client always holds and sends the complete current selection, so a diffing upsert would add complexity for no real benefit here. If you later want per-line history (e.g. "teacher removed the bus, then re-added it"), that's a separate audit-log concern, not a reason to change this endpoint's contract.


