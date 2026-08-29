# Decision log

One entry per real choice. Write it when you make the call, while you still
remember the alternative. This is a graded deliverable and the judges have said
the technical defence counts as much as the demo.

Format: what we chose, what we rejected, why, and what our choice still does not
solve. That last line is the one that scores. A team that names its own limits
reads as a team with judgment.

---

## 1. No model runs inside the enforcement path

**Chose:** A deterministic rules engine. Same input, same answer, every time.

**Rejected:** Letting the agent's model judge whether a purchase fits the mandate.

**Why:** The judges will operate this live and try to break it. A decision that
can vary between two identical requests is not a control, it is a suggestion.
It also answers the challenge's own question about what happens when the agent
hallucinates: the model can hallucinate a proposal, and the proposal still has
to pass a check it cannot influence.

**Does not solve:** The model still chooses what to propose, so a manipulated
agent wastes the buyer's time and fills the log with refusals.

---

## 2. What a mandate is

**Chose:**

**Rejected:** (candidates: a row in our database, a plain JWT, a JWT with a
delegation chain, a capability token such as a biscuit or macaroon, a full W3C
verifiable credential)

**Why:**

**Does not solve:**

---

## 3. How the person signs it

**Chose:**

**Rejected:** (candidates: passkey / WebAuthn with the mandate hash as the
challenge, a key held in the browser via WebCrypto, a server-side key we hold
on their behalf)

**Why:**

**Does not solve:**

---

## 4. How revocation reaches a purchase already in flight

**Chose:**

**Rejected:** (candidates: a flag the rules engine reads on every check,
short-lived spend authorisations that stop being minted, a published status list
the merchant polls)

**Why:**

**Does not solve:**

---

## 5. Where the rules live

**Chose:**

**Rejected:** (candidates: a hand-written typed evaluator, Cedar, Open Policy
Agent with Rego, JSON Logic)

**Why:**

**Does not solve:**

---

## 6. How the merchant verifies without trusting us

**Chose:**

**Rejected:** (candidates: the merchant calls our API and believes the answer,
the merchant verifies a signature itself against a published key, a shared secret)

**Why:**

**Does not solve:**

---

## 7. The shape of the audit log

**Chose:**

**Rejected:** (candidates: ordinary rows in the database, an append-only hash
chain with signed checkpoints, a real transparency log)

**Why:**

**Does not solve:**

---

## 8. Mocked payments rather than a real processor

**Chose:**

**Rejected:** (candidates: Stripe or another PSP in test mode)

**Why:** (the brief says catalogue, prices, mandates, protocols and payment
methods can all be invented, and test-mode keys, webhooks and tunnels cost hours
that buy no marks)

**Does not solve:**

---

## 9. Agent identity

**Chose:**

**Rejected:** (candidates: an API key, signed HTTP requests with keys published
at a well-known directory, mutual TLS)

**Why:**

**Does not solve:**
