# Contributing

## Developing

To make contributions to this charm, you'll need a working [development setup](https://documentation.ubuntu.com/juju/3.6/howto/manage-your-deployment/).

The code for this charm can be downloaded as follows:

```
git clone https://github.com/canonical/penpot-operator
```

Make sure to install [uv](https://docs.astral.sh/uv/), for example:

```sh
sudo snap install astral-uv --classic
```

Then install `tox` with extensions, as well as a range of Python versions:

```sh
uv tool install tox --with tox-uv
uv tool update-shell
```

To create an environment for development, use:

```shell
uv sync
source venv/bin/activate
```

## Generating src docs for every commit

Run the following command:

```bash
echo -e "tox -e src-docs\ngit add src-docs\n" >> .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## Testing

This project uses `tox` for managing test environments. There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

- `tox run -e format`: Update your code according to linting rules.
- `tox run -e lint`: Runs a range of static code analysis to check the code.
- `tox run -e unit`: Runs unit tests.
- `tox run -e integration`: Runs integration tests.
- `tox`: Runs `format`, `lint`, and `unit` environments.

## Build the charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

<!-- You may want to include any contribution/style guidelines in this document>
