FROM ubuntu:20.04

# Avoid interactive dialog from apt-get and other packages requiring configuration
ENV DEBIAN_FRONTEND=noninteractive

# install basic packages
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    vim \
    nano \
    unzip \
    zip \
    p7zip-full \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    python-is-python3 \
    build-essential \
    openssh-server \
    tmux \
    gettext \
    sudo \
    ffmpeg \
    libsm6 \
    libxext6 \
	dos2unix \
	libstdc++6 && rm -rf /var/lib/apt/lists/* \
    && pip install jupyter \
    && rm -rf /var/lib/apt/lists/* # removes cache

RUN pip install virtualenv \
    && wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh \
    && bash /tmp/miniconda.sh -b -p /opt/conda \
    && rm /tmp/miniconda.sh \
    && /opt/conda/bin/conda init

ARG CONDA_ENV_NAME=agent
ARG PYTHON_VERSION=3.10
ARG REQUIREMENTS=/tmp/requirements.txt

COPY environment/requirements.txt ${REQUIREMENTS}

# create conda environment and optionally install the requirements to it
RUN /opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main && \
    /opt/conda/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
RUN /opt/conda/bin/conda create -n ${CONDA_ENV_NAME} python=${PYTHON_VERSION} -y
ARG INSTALL_HEAVY_DEPENDENCIES=true
ENV INSTALL_HEAVY_DEPENDENCIES=${INSTALL_HEAVY_DEPENDENCIES}

# The rest of your Dockerfile
RUN if [ "$INSTALL_HEAVY_DEPENDENCIES" = "true" ]; then \
    # Configure conda channels
    /opt/conda/bin/conda config --add channels conda-forge    && \
    /opt/conda/bin/conda config --add channels tudat-team    && \
    /opt/conda/bin/conda config --set channel_priority strict && \
    
    # Install all heavy dependencies in a SINGLE command.
    # This is much faster and far more reliable, as it only
    # solves dependencies and fetches packages once.
    /opt/conda/bin/conda run -n ${CONDA_ENV_NAME} conda install -y \
      libstdcxx-ng \
      tudatpy \
      pykep \
      poliastro \
      astropy \
      skyfield \
      casadi \
      pygmo \
      dymos \
      ipopt && \
    
    # Install any remaining pip requirements
    /opt/conda/bin/conda run -n ${CONDA_ENV_NAME} pip install -r /tmp/requirements.txt && \
    
    # Clean up conda cache
    /opt/conda/bin/conda clean -afy ; fi
	
	# This permanently sets the LD_PRELOAD variable *for this environment*
    # It forces the OS to use the conda-provided libstdc++ library to fix pykep import error
# This command runs in the shell and finishes
RUN /opt/conda/bin/conda run -n ${CONDA_ENV_NAME} conda env config vars set LD_PRELOAD=/opt/conda/envs/${CONDA_ENV_NAME}/lib/libstdc++.so.6

# This is a separate Dockerfile instruction that sets the PATH for the next steps
ENV PATH="/opt/conda/bin:${PATH}"

# Installs from here onward go into the conda base env; previous was installed to /usr/bin/python

# Install stuff for the grading server: mlebench and flask
COPY . /mlebench
RUN pip install flask && pip install -e /mlebench


# Reset DEBIAN_FRONTEND
ENV DEBIAN_FRONTEND=

# Make private directory (root) owner-only. Grading server will be added here, later in the build
# The test set answers will be added here separately via a mounted docker volume
RUN mkdir /private && chmod 700 /private

# Copy over relevant files
COPY environment/grading_server.py /private/grading_server.py
COPY environment/instructions.txt /home/instructions.txt
COPY environment/instructions_obfuscated.txt /home/instructions_obfuscated.txt
COPY environment/validate_submission.sh /home/validate_submission.sh
COPY environment/entrypoint.sh /entrypoint.sh


# convierto saltos de liena a linux
RUN dos2unix /entrypoint.sh
RUN dos2unix /mlebench/agents/aide/start.sh
# Create nonroot user; make entrypoint executable
RUN useradd -m nonroot \
    && mkdir /home/submission \
    && chmod +x /entrypoint.sh

WORKDIR /home

# IMPORTANT: This needs to run as root! Downstream Dockerfiles must not change the default USER for when the container starts.
# Entrypoint script is in charge of setting up the user environment and running the grading server
ENTRYPOINT ["/entrypoint.sh"]
