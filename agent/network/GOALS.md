# Operator goals

This file is the operator's alone: the operator writes here, Last Order reads. The
administrator cannot edit it — every open line below enters the network goals with the
mark "оператор" in `why`, until it is done and removed here. One goal per line:

- <goal text> — <why, optional>

<!-- Operator: to add or retire a goal,
     docker compose -f docker/compose.yml exec last-order sh -c \
       'printf -- "- <goal text>\n" >> /workplace/GOALS.md'
     and edit the file the same way to remove a done line. Nothing else is needed:
     the alarm wakes Last Order on the change. -->
