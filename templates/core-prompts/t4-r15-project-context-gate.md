Decide whether the user just started work on a project without the agent having pulled the project context (atlas, git log, project files). Answer "yes" if a context pull is required, "no" if the turn is conversational / off-topic / meta.

Examples:
+ User: "Vamos a arreglar el bug del checkout" -> yes
+ User: "Hazme un refactor del login de CanaRirural" -> yes
+ User: "Revisa la PR del orchestrator" -> yes
- User: "qué hora es" -> no
- User: "gracias, ya está" -> no
- User: "dime un chiste" -> no

Now decide. Input:
[[span]][[context_section]]

Answer exactly "yes" or "no".
