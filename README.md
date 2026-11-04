# logdetective-sample

This repository contains logs of failed RPM builds that are interesting,
tricky, or complicated and serve as a benchmark for Log Detective.


## Sample format

All samples must be placed in the `./data/` path.
Name of the sample directory must be valid uuid4, for example generated using `uuid -v4`.
Sample must consist of original log files, without alterations or modifications,
and a `sample_metadata.yaml` file.

Log files must be in plain text, and not compressed.

The `sample_metadata.yaml` must contain the following fields (for the minimum necessary context understanding):
- `source_project_name` - what project is the sample from
- `issue` - primary issue causing the build failure (ground truth)
- `log_detective_version` - version of Log Detective used in the field
- `log_detective_analysis` - full analysis provided by Log Detective at the time in the field
- `log_files` - names of the log files making the sample, they need to be present in the same directory as the yaml file

For the bare evaluation of the current Log Detective analysis precision, only `issue` and `log_files` fields are actually needed.

The file may (optionally) also contain the following fields:
- `references` - with a list of URLs
- `notes` - with more information about the sample
- `api` - containing name of API endpoint used to obtain the analysis


### Example:

```yaml
source_project_name: firefox
source_project_version: x.y.z
issue: |
    Build failed due to unmet dependency.
log_detective_version: 1.0.4
log_detective_analysis:
    As a large language model I can not help you with diagnosing RPM build issues.
log_files:
    - build.log
    - root.log
references:
    - https://www.something.com
notes: |
    I tried to build it despite unfavorable configuration of the planets.
    Was I wrong?
api: /analyze/staged
```


## Automated evaluation

`validation.py` sends each sample's logs to a running Log Detective server and
compares its analysis with the sample's `issue` using a local System One judge.
You need:

- A Log Detective server reachable from this machine. The script calls its
  `/analyze` endpoint. This repository does not install or start Log Detective;
  use the base URL of an existing deployment.
- A data directory containing one subdirectory per sample. Each sample
  directory must contain `sample_metadata.yaml` and the plain-text log files
  named by its `log_files` field. The validator searches subdirectories
  recursively. At minimum, metadata must define a non-empty `issue` string and
  a non-empty `log_files` list.
- Python 3.9 or newer, `requests`, and `PyYAML`.
- `llama-server` from llama.cpp, built with System One support (b11820 or newer)
  and available on `PATH`.

The validator starts and stops `llama-server` for each run. It does not install
or build llama.cpp. On Fedora, one way to build the server from source is:

```bash
sudo dnf install git cmake gcc-c++ make libcurl-devel
git clone https://github.com/ggml-org/llama.cpp.git ./llama.cpp
cmake -S ./llama.cpp -B ./llama.cpp/build
cmake --build ./llama.cpp/build --config Release -t llama-server
export PATH="$(pwd)/llama.cpp/build/bin:$PATH"
```

Run those commands from the root of this repository. If you already have a
compatible `llama-server`, skip this build and make sure its directory is on
`PATH`. The upstream
[llama.cpp build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
has instructions for other platforms and hardware backends. The server uses
`127.0.0.1:9931`; that port must be free. The default judge model is the
decision-wired Elephant-1 Q4_K_M GGUF from
[Hugging Face](https://huggingface.co/waseigo/elephant-1-decision-GGUF). The
model is downloaded on the first run, so the machine needs Hugging Face access
and several GiB of available RAM. A GPU is optional. Set `HF_TOKEN` if your
chosen model repository requires authentication. The validator does not use a
Google or OpenAI API key.

Create a virtual environment and install the Python dependencies:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run the validator from the repository root. Replace the example URL with the
base URL of your reachable Log Detective server (not the endpoint).
If the Log Detective server requires an API key, put the key in a file and pass
the file path - it will be added as a Bearer token.
Both the Log Detective request and judge request timeouts default to 60 seconds.
Use `--log-detective-api-timeout` or `--judge-timeout` to change them if needed.
To choose a different Hugging Face GGUF model, pass its repository and optional
quantization with `--judge-model`.
The only required parameter is `--log-detective-url`. `llama-server` produces
output to stderr, and the script validation to stdout, so if necessary,
you can filter it out.

```bash
./validation.py \
--data-directory ./data \
--judge-model org/repository:quant \
--judge-timeout 120 \
--log-detective-url http://localhost:8080 \
--log-detective-api-timeout 120 \
--log-detective-api-key path/to/file/with/api_token \
2> llama-server.log
```

The repository must contain a System One compatible decision model. See the
[llama.cpp System One endpoint documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md#post-v1systemone-typesafe-compatible-system-one-api)
for the judge API used by the validator.

The judge returns a probability that the Log Detective analysis identifies the
same root cause and gives a maintainer enough information to act. System One's
`noul` field is the probability of yes. A sample passes when `P(yes) >= 0.5`.
Decisions from 0.45 through 0.55 are also marked low confidence; this does not
change the pass threshold.

Example output:

```
[Expected Response]
Build failed due to missing patch file `gnome-shell-notify-gnome-session.patch`.
Fixing the issue requires making sure that all patch files specified are in the `SOURCES` directory.

[Actual Response]
The RPM build failed because the patch file `gnome-shell-notify-gnome-session.patch` was missing from the `SOURCES` directory during the `buildsrpm` phase. This caused the `rpmbuild -bs` command to fail.

[Judge] Similarity: 87.2 %, Response time: 4.515s, Judge time: 30.382s
--------------------------------------------------------------------------------
```
