#! /usr/bin/env bash

################################################################################
# Script Name    : build_and_push_docker.sh
#-------------------------------------------------------------------------------
# Description    : This script builds the Docker image and pushes it to DockerHub
################################################################################

# ---------------------------------------------------------------------------- #
#                                   Colors                                       #
# ---------------------------------------------------------------------------- #
COLOR_RED="\e[1;31m"
COLOR_GREEN="\e[1;32m"
COLOR_YELLOW="\e[1;33m"
COLOR_RESET="\e[0m"

# ---------------------------------------------------------------------------- #
#                                   Constants                                    #
# ---------------------------------------------------------------------------- #
DOCKER_USERNAME="ezalos"
IMAGE_NAME="work.gpu"
DOCKERFILE_PATH="Dockerfile.cuda112"

# Version handling
IMAGE_VERSION=${1:-latest}
FULL_IMAGE_NAME="${DOCKER_USERNAME}/${IMAGE_NAME}"

# ---------------------------------------------------------------------------- #
#                                   Variables                                    #
# ---------------------------------------------------------------------------- #
echo -e "    Variables:"
echo -e "\t${COLOR_YELLOW}DOCKERFILE_PATH         ${COLOR_RESET}${DOCKERFILE_PATH}"
echo -e "\t${COLOR_YELLOW}DOCKER_USERNAME         ${COLOR_RESET}${DOCKER_USERNAME}"
echo -e "\t${COLOR_YELLOW}IMAGE_NAME              ${COLOR_RESET}${IMAGE_NAME}"
echo -e "\t${COLOR_YELLOW}IMAGE_VERSION           ${COLOR_RESET}${IMAGE_VERSION}"
echo -e "\t${COLOR_YELLOW}FULL_IMAGE_NAME         ${COLOR_RESET}${FULL_IMAGE_NAME}"

# ---------------------------------------------------------------------------- #
#                                 Docker login                                   #
# ---------------------------------------------------------------------------- #
echo -e "${COLOR_YELLOW}B&P: ${COLOR_RESET}Connecting to DockerHub..."

if [ -z "$DOCKER_PASSWORD" ]; then
    echo -e "${COLOR_YELLOW}Please enter your DockerHub password: ${COLOR_RESET}"
    read -r -s DOCKER_PASSWORD
    echo
fi

if echo "$DOCKER_PASSWORD" | docker login -u "$DOCKER_USERNAME" --password-stdin; then
    echo -e "${COLOR_GREEN}Docker login successful${COLOR_RESET}"
else
    echo -e "${COLOR_RED}ERROR: Docker login failed${COLOR_RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------- #
#                                 Docker build                                   #
# ---------------------------------------------------------------------------- #
echo -e "${COLOR_YELLOW}B&P: ${COLOR_RESET}Building container..."

export DOCKER_TAG=$IMAGE_VERSION

if docker compose build; then
    echo -e "${COLOR_GREEN}Docker build successful${COLOR_RESET}"
else
    echo -e "${COLOR_RED}ERROR: Docker build failed${COLOR_RESET}"
    exit 1
fi

# ---------------------------------------------------------------------------- #
#                                  Docker push                                   #
# ---------------------------------------------------------------------------- #

# Push version
echo -e "${COLOR_YELLOW}B&P: ${COLOR_RESET}Tag & Push container:${IMAGE_VERSION} ..."

if docker push "${FULL_IMAGE_NAME}:${IMAGE_VERSION}"; then
    echo -e "${COLOR_GREEN}Docker push version successful${COLOR_RESET}"
else
    echo -e "${COLOR_RED}ERROR: Docker push version failed${COLOR_RESET}"
    exit 1
fi

# If we're building a specific version, also tag it as latest
if [ "$IMAGE_VERSION" != "latest" ]; then
    echo -e "${COLOR_YELLOW}B&P: ${COLOR_RESET}Tag & Push container:latest ..."

    if docker tag "${FULL_IMAGE_NAME}:${IMAGE_VERSION}" "${FULL_IMAGE_NAME}:latest" && \
       docker push "${FULL_IMAGE_NAME}:latest"; then
        echo -e "${COLOR_GREEN}Docker tag and push latest successful${COLOR_RESET}"
    else
        echo -e "${COLOR_RED}ERROR: Docker tag and push latest failed${COLOR_RESET}"
        exit 1
    fi
fi

echo -e "\nYou can now pull these images using:"
echo -e "${COLOR_YELLOW}docker pull ${FULL_IMAGE_NAME}:${IMAGE_VERSION}${COLOR_RESET}"
if [ "$IMAGE_VERSION" != "latest" ]; then
    echo -e "${COLOR_YELLOW}docker pull ${FULL_IMAGE_NAME}:latest${COLOR_RESET}"
fi 