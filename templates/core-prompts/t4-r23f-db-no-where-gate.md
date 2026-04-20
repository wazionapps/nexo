Decide whether the SQL statement performs DELETE/UPDATE without a WHERE clause against a production table. Answer "yes" if it is an unscoped destructive write, "no" if it is a well-scoped delete, a DDL command, or a scratch table known to be temporary.

Examples:
+ `DELETE FROM orders` -> yes
+ `UPDATE users SET active=0` -> yes
+ `DELETE FROM clients` -> yes
- `DELETE FROM orders WHERE id = 123` -> no
- `TRUNCATE TABLE tmp_scratch` -> no
- `UPDATE users SET last_login = NOW() WHERE id = 42` -> no

Now decide. Input:
[[span]][[context_section]]

Answer exactly "yes" or "no".
