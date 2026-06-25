# Randoku Sidecar Constitution

## Preamble

Randoku Sidecar exists to help AI assistants perform engineering work safely, predictably, and transparently.

AI models are becoming increasingly capable.

Trustworthy engineering workflows, however, are built on explicit permissions, reviewable changes, auditable execution, and human judgment.

This Constitution defines the engineering principles that every capability, workflow, and future contribution must follow.

**Code follows contracts. Contracts follow reason. Reason follows evidence.**

**Trust is earned through review, not assumed through intelligence.**

---

## Article I — Policy Before Capability

A new capability must never be implemented before its governing policy exists.

Shared policy comes before shared implementation.

---

## Article II — Capability Contract

Every capability must define:

- Purpose
- Authority
- Preconditions
- Postconditions
- Failure Contract
- Rollback Strategy
- Audit Record
- Behavior Preservation

A capability without a contract should not be implemented.

---

## Article III — Reason Before Agreement

The assistant must reason before agreeing.

Agreement is the outcome of analysis, not the starting point.

Disagreement, uncertainty, and alternative designs are valuable when supported by evidence.

---

## Article IV — Human Approval Boundary

High-risk operations remain under explicit human approval.

Examples include:

- git push
- deployment
- production changes
- credential handling
- system configuration
- destructive operations

Automation assists decisions.

It does not replace ownership.

---

## Article V — Behavior Preservation

Refactoring must demonstrate that important behavior has not changed.

Reviews should explicitly verify:

- Permission checks
- Security boundaries
- Exception handling
- Compatibility
- Side effects

Passing tests alone is insufficient.

---

## Article VI — Auditability

Every state-changing capability should leave sufficient audit information to explain:

- what changed
- why it changed
- how it was executed
- how it can be reverted

---

## Article VII — Least Privilege

Read-only is the default.

Additional authority must be explicitly justified.

---

## Article VIII — Simplicity

Prefer small, verifiable capabilities over large, generic tools.

Incremental evolution is preferred over large rewrites.

---

## Amendment Process

Changes to this Constitution require justification.

Every amendment should explain:

- what problem the previous rule could not solve
- why the new rule is better
- what risks are introduced
- how those risks will be validated
