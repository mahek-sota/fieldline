"""Print a saved report as plain text. Usage: python summary.py web/report.json"""
import json
import sys

r = json.load(open(sys.argv[1]))
lt = r["lost_time"]
print(f"\nSite {r['site']['start'][:10]} to {r['site']['end'][:10]}, {r['site']['events']} events\n")
print("Units installed per day")
for d in r["units_per_day"]:
    print(f"  {d['date']}  {d['units']:4d}")
print(f"\nLost time: {lt['total_hours']} h  (machine faults {lt['machine_fault_hours']} h, "
      f"waiting for material {lt['waiting_for_material_hours']} h)")
for x in lt["by_reason"]:
    print(f"  {x['reason']:<14} {x['count']:3d} stops  {x['hours']:5.2f} h")
print("\nStations (hours)")
print(f"  {'station':<10}{'units':>7}{'cycle s':>9}{'busy':>8}{'fault':>8}{'load':>8}")
for s in r["stations"]:
    print(f"  {s['station']:<10}{s['units']:>7}{s['mean_cycle_s']:>9}{s['busy_hours']:>8}{s['fault_hours']:>8}{s['load_hours']:>8}")
print(f"\nBottleneck: {r['bottleneck']['station']} ({r['bottleneck']['rule']})")
print(f"\nDrift ({r['drift']['method']})")
if not r["drift"]["alarms"]:
    print("  no alarms")
for a in r["drift"]["alarms"]:
    print(f"  {a['station']}: alarm at {a['alarm_at']} (batch mean {a['batch_mean_at_alarm_s']} s vs baseline {a['baseline_cycle_s']} s)")
    fx = a["fixed_by_fault"]
    if fx:
        print(f"    faulted {fx['started']} with {fx['reason']}, fixed by that repair")
        print(f"    warning: {a['warning_hours']} h on the clock, {a['warning_operating_hours']} h of operation")
    else:
        print("    still open at end of data")
    for f in a["other_faults_during_alarm"]:
        print(f"    other stop during alarm: {f['reason']} at {f['started']} (cycle time stayed high after)")
