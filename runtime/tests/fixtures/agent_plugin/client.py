import sys
if sys.argv[1] == '--version':
    print('fixture-agent 1.2.3')
elif sys.argv[1] == 'status':
    print('ready')
elif sys.argv[1] == 'ask':
    print('test fixture output: ' + sys.argv[2] + '/' + sys.argv[3])
else:
    raise SystemExit(1)
