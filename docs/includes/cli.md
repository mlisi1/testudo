## `testudo`

```text
usage: testudo [-h] [-v] [--no-hz] {watch,replay,plugins,config} ...

Black-box diagnostics for navigation stacks.

positional arguments:
  {watch,replay,plugins,config}
    watch               live interactive TUI (or --headless: publish only)
    replay              batch report over a bag, or --watch to scrub it live
    plugins             list discovered plugins and the msg types they cover
    config              work with the config file

options:
  -h, --help            show this help message and exit
  -v, --verbose         enable debug logging
  --no-hz               don't monitor or report topic publish rate (Hz)
```

## `testudo watch`

```text
usage: testudo watch [-h] [-c CONFIG] [--no-hz] [--headless]

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        path to the Testudo YAML config file (default:
                        /home/elechim/.config/testudo/config.yaml)
  --no-hz               don't monitor or report topic publish rate (Hz)
  --headless            no TUI: keep monitoring and publishing diagnostics
                        until Ctrl-C (robots, launch files, CI)
```

## `testudo replay`

```text
usage: testudo replay [-h] [-c CONFIG] [--watch] bag

positional arguments:
  bag                   path to the bag to replay

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        path to the Testudo YAML config file (default:
                        /home/elechim/.config/testudo/config.yaml)
  --watch               scrub through the bag in the TUI
```

## `testudo plugins`

```text
usage: testudo plugins [-h] [-c CONFIG]

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        path to the Testudo YAML config file (default:
                        /home/elechim/.config/testudo/config.yaml)
```

## `testudo config`

```text
usage: testudo config [-h] {init} ...

positional arguments:
  {init}
    init      write a config for the stack running now

options:
  -h, --help  show this help message and exit
```

## `testudo config init`

```text
usage: testudo config init [-h] [-c CONFIG] [--force]

options:
  -h, --help            show this help message and exit
  -c CONFIG, --config CONFIG
                        path to the Testudo YAML config file (default:
                        /home/elechim/.config/testudo/config.yaml)
  --force               replace the file if it already exists
```
