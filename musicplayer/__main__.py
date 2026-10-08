import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from musicplayer.ui.app import main
    raise SystemExit(main())
