$execute as @e[type=marker,tag=lo_rig,limit=1] at @s rotated ~$(speed) 0 positioned as @p[tag=lo_target] run tp @s ~ ~ ~ ~ ~
$execute as @e[type=marker,tag=lo_rig,limit=1] positioned as @p[tag=lo_target] rotated as @s run tp LoCamera ^ ^$(h) ^-$(r) facing entity @p[tag=lo_target] eyes
