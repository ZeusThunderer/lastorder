# modes: 1 orbit around the target, 2 dolly along the camera's facing, 3 tracking pan from a fixed point,
# 4 follow at a world-fixed offset. Parameters: storage lo_cam:cfg. Target: the player tagged lo_target.
execute if score #mode lo_cam matches 1 run function lo_cam:orbit with storage lo_cam:cfg
execute if score #mode lo_cam matches 2 run function lo_cam:dolly with storage lo_cam:cfg
execute if score #mode lo_cam matches 3 as LoCamera at @s run tp @s ~ ~ ~ facing entity @p[tag=lo_target] eyes
execute if score #mode lo_cam matches 4 run function lo_cam:follow with storage lo_cam:cfg
