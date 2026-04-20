# Email Draft Template

Use this template when preparing a human-facing email draft, an automation-owned outbound message, or a reviewable reply before sending.

Keep it generic:

- Do not hardcode business-specific wording into the template.
- Do not promise actions that have not happened yet.
- Do not expose secrets, internal paths, or runtime details.
- Use the sender identity that the email-account contract actually allows (`agent` account by default, operator inbox only when `can_send=true`).

## Metadata

```text
To:
Cc:
Bcc:
From label:
Language:
Tone:
Goal:
Attachments:
Requires approval: yes|no
```

## Subject

```text
Short, specific subject line
```

## Body

```text
Hello {{recipient_name}},

{{opening_context}}

{{main_message}}

{{requested_action_or_next_step}}

Best regards,
{{sender_name}}
{{sender_role}}
```

## Review Checklist

- The recipient is explicit or intentionally resolved through the default route.
- The language matches the recipient context.
- The body contains one clear goal.
- The request or next step is explicit.
- The signature matches the allowed sending identity.
- The draft does not claim completion without evidence.
