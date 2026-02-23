# Architecture Decision Records (ADRs)

This directory contains Architecture Decision Records (ADRs) for the Sunbeam OpenStack project.

## What is an ADR?

An Architecture Decision Record (ADR) is a document that captures an important architectural decision made along with its context and consequences.

## Format

We follow the [MADR (Markdown Architectural Decision Records)](https://adr.github.io/madr/) format. Each ADR includes:

- **Title**: Brief description of the decision (verb phrase format)
- **Status**: Proposed | Accepted | Deprecated | Superseded
- **Context and Problem Statement**: What issue motivates this decision
- **Decision Drivers**: Forces and requirements influencing the decision
- **Considered Options**: All alternatives evaluated
- **Decision Outcome**: The chosen option with consequences and confirmation criteria
- **Pros and Cons of Options**: Detailed comparison of rejected alternatives

## Creating a New ADR

1. **Copy the template**:
   ```bash
   cp docs/adr/adr-template.md docs/adr/NNNN-short-title.md
   ```
   Use the next sequential number (e.g., 0002, 0003) for `NNNN`.

2. **Fill in the template**:
   - Use present tense imperative for the title (e.g., "Use React for UI", not "Using React")
   - Be specific about the problem and context
   - List all options you considered, not just the chosen one
   - Explain *why* you chose this option

3. **Set initial status** to "proposed"

4. **Create a PR** for review and discussion

5. **Update status** to "accepted" when merged

## File Naming Convention

- Format: `NNNN-title-with-dashes.md`
- `NNNN`: Sequential number (0001, 0002, ...)
- Use lowercase and dashes for the title
- Use `.md` extension

Examples:
- `0001-feature-gates-framework.md`
- `0002-use-terraform-for-deployment.md`

## Index

- [ADR-0001](0001-feature-gates-framework.md) - Use Feature Gates Framework for Sunbeam

## Updating an ADR

ADRs should be immutable once accepted. If a decision changes:

1. Create a new ADR that supersedes the old one
2. Update the old ADR's status to "superseded by ADR-NNNN"
3. Link the new ADR back to the old one in "Related Decisions"

## Questions?

See the [Contributing Guide](../CONTRIBUTING.md) for more information.

