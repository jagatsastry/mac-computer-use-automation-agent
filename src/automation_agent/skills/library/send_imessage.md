---
name: send-imessage
skill-id: send-imessage
description: Send an iMessage to a contact
summary: Open the Messages app, compose a new message to a recipient, and send text content via iMessage.
tags: [messaging, imessage, communication, messages]
trigger-keywords: [imessage, text, message, send message]
parameters:
  recipient:
    type: string
    required: true
    description: Contact name or phone number
    examples: ["Mom", "+1234567890"]
  message:
    type: string
    required: true
    description: Message text to send
    examples: ["Running late!", "See you at 5"]
requires:
  apps: [Messages]
  os: darwin
success-condition: Message sent confirmation visible
max-retries: 2
---

## Steps
1. Open Messages app
   - verify: Messages app is frontmost
2. Press Cmd+N to start a new message
   - verify: New message compose window visible
3. Type "{{recipient}}" in the To field and press Enter
   - verify: Recipient selected
4. Click the message input field
   - verify: Message input focused
5. Type "{{message}}" and press Enter
   - verify: Message appears in conversation

## Error Recovery
- If recipient not found: try again with full name
- If Messages not responding: force quit and reopen
