from simulatte.environment import Environment


def test_environment():
    env_1 = Environment()
    env_2 = Environment()
    assert env_1 is env_2

    Environment.clear()
    env_3 = Environment()
    assert env_1 is not env_3


def test_processes():
    def phase_1(e):
        yield e.timeout(10)

    def phase_2(e):
        yield e.timeout(20)

    def phase_3(e):
        yield e.timeout(100)

    env = Environment()
    env.process(phase_1(env))
    env.run()

    assert env.now == 10

    env.process(phase_2(env))
    env.run()

    assert env.now == 30

    Environment.clear()
    env2 = Environment()
    env2.process(phase_3(env2))
    env2.run()

    assert env is not env2
    assert env2.now == 100
