"""Makes ``tests`` a real package so pytest's default `prepend` import mode
qualifies every test module by its package path (e.g. ``tests.core.test_x``)
instead of its bare basename — required before test files can be mirrored
into subdirectories without basename collisions (T-70).
"""
