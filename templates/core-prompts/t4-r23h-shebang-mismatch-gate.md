Decide whether the shebang of the script disagrees with the interpreter that will actually be invoked. Answer "yes" if the mismatch will break execution, "no" otherwise.

Examples:
+ "#!/usr/bin/env python3" + bash body with `for i in $(seq 1 10); do` -> yes
+ "#!/bin/sh" + bashisms like `[[ ${foo} == "bar" ]]` -> yes
+ "#!/usr/bin/env node" + bash heredoc body -> yes
- "#!/usr/bin/env python3" + real Python body -> no
- "#!/bin/bash" + bash arrays -> no
- Python script with no shebang at all -> no

Now decide. Input:
[[span]][[context_section]]

Answer exactly "yes" or "no".
