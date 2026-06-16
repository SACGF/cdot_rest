# cdot_rest

REST server for [cdot](https://github.com/SACGF/cdot/).

We host historical versions of RefSeq and Ensembl transcripts (GRCh37, GRCh38 and T2T-CHM13v2.0)
to resolve [HGVS](http://varnomen.hgvs.org/).

Public instance: **https://cdotlib.org**

## API

See the [API documentation](https://cdotlib.org/static/api-docs.html) (served from `/static/api-docs.html`).

Transcript data is stored in Redis. The front page shows the loaded
[cdot data release](https://github.com/SACGF/cdot/releases).

## Loading data

Run from the project directory inside the virtual environment:

```bash
sudo su cdot
cd /opt/cdot_rest
source .venv/bin/activate
```

### Latest (recommended)

Download the latest cdot data [release](https://github.com/SACGF/cdot/releases) (RefSeq and
Ensembl, GRCh37 + GRCh38 + T2T-CHM13v2.0) straight from GitHub and load it. The release version is
recorded and shown on the front page:

```bash
python3 manage.py import_transcript_json latest
```

### A specific file

Download a file from the [cdot releases](https://github.com/SACGF/cdot/releases) (or
[create the data from scratch](https://github.com/SACGF/cdot/wiki/Create-data-from-scratch)),
then load it, passing the consortium and data version:

```bash
python3 manage.py import_transcript_json cdot_json \
    --annotation-consortium=RefSeq --cdot-data-version=0.2.32 \
    cdot-0.2.32.refseq.GRCh38.json.gz

python3 manage.py import_transcript_json cdot_json \
    --annotation-consortium=Ensembl --cdot-data-version=0.2.32 \
    cdot-0.2.32.ensembl.GRCh38.json.gz
```

The same accession can appear in multiple per-build files (eg a RefSeq transcript in both the
GRCh37 and GRCh38 files); the loader merges their `genome_builds` rather than overwriting, so you
can load builds separately.

Because loading is additive, upgrading to a new release on top of an old one leaves stale data
behind (accessions dropped in the new release, drifting counts). Pass `--clear` to flush the Redis
db first for a clean reload — it works with either subcommand and clears before loading anything:

```bash
python3 manage.py import_transcript_json latest --clear
```

To copy files onto the server first:

```bash
scp -i ~/.ssh/variantgrid-cloud.pem \
    cdot-0.2.32.refseq.GRCh38.json.gz cdot-0.2.32.ensembl.GRCh38.json.gz \
    ubuntu@cdotlib.org:/data/incoming
```

## Install

```bash
sudo bash
apt-get update
apt-get upgrade -y

apt-get install -y git-core redis nginx

# uv (https://docs.astral.sh/uv/) - manages the Python virtual environment
curl -LsSf https://astral.sh/uv/install.sh | sh

# User and source code
SYSTEM_CDOT_USER=cdot
CDOT_REST_INSTALL_DIR=/opt/cdot_rest

id -u ${SYSTEM_CDOT_USER} &>/dev/null || useradd ${SYSTEM_CDOT_USER} --create-home --shell "/bin/bash"
mkdir -p ${CDOT_REST_INSTALL_DIR}
chown ${SYSTEM_CDOT_USER} ${CDOT_REST_INSTALL_DIR}
chgrp ${SYSTEM_CDOT_USER} ${CDOT_REST_INSTALL_DIR}

su ${SYSTEM_CDOT_USER}
CDOT_REST_INSTALL_DIR=/opt/cdot_rest  # Again, for the user
cd ${CDOT_REST_INSTALL_DIR}
if [ ! -e ${CDOT_REST_INSTALL_DIR}/.git ]; then
    git clone https://github.com/sacgf/cdot_rest.git .
fi

# Python libraries (gunicorn is included in requirements.txt)
uv venv .venv
source /opt/cdot_rest/.venv/bin/activate
uv pip install -r requirements.txt
```

> **Note:** `requirements.txt` currently pins `cdot` to its git `main` branch — the `latest`
> loader needs `get_latest_combo_file_urls` / `get_latest_data_version_and_release`, which are not
> yet in a PyPI release. Re-pin to a PyPI version once one is published.

### Services

```bash
# As root
mkdir /var/log/cdot_rest
chown cdot /var/log/cdot_rest
mkdir /run/gunicorn
chown cdot /run/gunicorn/

cp config/gunicorn.service /lib/systemd/system
systemctl enable gunicorn
systemctl start gunicorn
```

nginx terminates TLS and proxies to gunicorn — see [`config/nginx.conf`](config/nginx.conf).

## Development

```bash
uv pip install -r requirements-test.txt
python3 manage.py test
```
