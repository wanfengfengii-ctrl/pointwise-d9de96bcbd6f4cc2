# 北湾冷库告警交班站

北湾食品质控中心的值班员为试验库录制绑定固定告警规程，在线换规，并跟踪封口先到时的待件状态。质控员凭清单号核对原读、出处、告警次数和最高温度。

```sh
python3 -B check_project.py
python3 -B demo.py --temporary
python3 -B custody_demo.py --temporary
```

`check_project.py` 现在也运行 `tests/test_custody.py` 的双库托管、失答重投、腾退和停源领取核验。`custody_demo.py` 使用两个临时 SQLite 库演示 CUSTODY.md 中的缺件、确认腾退、补收和班报复领；原 `demo.py --temporary` 仍演示单库在线换规。

运行环境为 Python 3.11 标准库。单库演示也可用 `python3 -B demo.py --db ./demo.sqlite3` 明确指定一个新文件。演示采用 `sample.json` 的固定输入，输出实际签发结果及按原号复核的结果。

## 班次操作

1. 用 `storage.connect(path)` 打开 SQLite 库，调用 `storage.initialize(db)` 创建或打开表。重复初始化保留已登记修订、录制游标、来件和清单。可用不同连接分别办理控制和设备事件。
2. 用 `rules.catalog.register_rule(db, rule_id, revision, point, threshold_mc, unit='mC')` 登记固定规则。同键同参返回原引用，同键异参报 `ValueError`。阈值及原读均为整数毫摄氏度，闭阈值只适用于指定测点。
3. 用 `control.shifts.open_shift(db, recording, shift_id, rule)` 开班，未知修订报 `KeyError`，占用冲突报 `ValueError`。`get_shift(db, shift_id)` 查询原绑定。设备调用 `recorder.recording.record_reading(db, recording, point, temperature_mc, source_seq=None, unit='mC')` 采样，返回实际匹配的逐路投递。指定源序时必须等于下一序号。未命中仍推进源序，非法输入或停源后追加报 `ValueError`。
4. 用 `control.changeover.change(db, recording, new_rule, operation_id, device=None)` 发起或续办。`ChangeOperation.boundary` 为 `None` 表示待办。同号异参、另单在办或终止后新办报 `ValueError`。默认设备端口直接办理，独立端口可延迟 `request` 或 `lookup` 的结果，原号查询会取回已经提交的动作。旧路在查实新路启用前保持活动。启用界 A 之后新路有覆盖，停用界 D 及以前旧路有覆盖。本系统固定 B=A，区段归属按 B 裁决。同号重开沿用同一结果。后续串行换规会生成新路，原物理槽的历史绑定仍可查。
5. `intake.receipts.receive(db, item)` 接收一个 `ReadingDelivery` 或 `LaneSeal`，返回具有 `status` 和原来件的收件结果。有效新件为 `accepted`，原样重投为 `duplicate`。冲突或未知来源报 `ValueError`。`read_lane_receipts`、`read_lane_seals` 分别查询实收事实，路的封口可与原读独立投递。同一源读的两个投递来源分别保留。
6. `recorder.recording.stop_recording(db, recording)` 冻结最终源序并返回逐路末封口。封口含截止源序 C 和截至 C 的末投递序，末序 0 是有效空封口。已受理的动作可以在封班后的冻结游标结清。用 `control.closure.confirm_shift_end(db, shift_id)` 接收设备终点，设备尚未结束时返回 `None`。原读和封口仍由收件入口独立接纳。
7. `archive.publication.publish(db, shift_id, report_id)` 核对该班次的每段负责路。封口须覆盖段末，可能落在区段内的缺号继续待件，已证实在区段外的缺号可跳过。待件返回 `None`。齐备后返回报告、明细、实际界、条数、最高温及首执。温度排序和汇总均为整数计算，空报告的最高温为 `None`。同班同号重试取回原报告，跨班占用同号报 `ValueError`。SQLite 提交失败向调用者报告，所有新签存一同回滚。
8. `archive.history.get_report(db, report_id)` 按原号领取冻结快照，未签号返回 `None`。断源、新修订登记和重新打开数据库均可沿用已签结果。

`tests/test_record_shift.py` 检查普通班、空班和规程绑定，`tests/test_changeover.py` 检查实采交接及逐路齐备，`tests/test_intake.py` 检查冲突和晚到验源，`tests/test_handover.py` 检查提交失败、原号复领和重复初始化。
