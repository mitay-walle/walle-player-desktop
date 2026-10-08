import multiprocessing
multiprocessing.freeze_support()
from musicplayer.ui.app import main
raise SystemExit(main())
