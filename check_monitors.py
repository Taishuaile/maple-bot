import mss

with mss.mss() as s:
    print('All monitors:')
    for i, m in enumerate(s.monitors):
        print(i, m)
