# VDL Project Template

A template repository for all VDL projects. This template provides the basic structure and setup instructions for new VDL projects.

## Repository Layout

The project follows a specific processing pipeline with the following order of operations:

1. **Data Preprocessing**
   - Location: `data_preprocessing`
   - Purpose: Initial data cleaning and preparation for creating a unified dataset.
   - Main Script: `process_data.py`

2. **Data Enrichment**
   - Location: `data_enrichment`
   - Purpose: Adding additional features and transformations to the preprocessed data
   - Main Script: `enrich_data.py`

3. **Player Building**
   - Location: `player_building`
   - Purpose: Final player construction and output generation
   - Main Script: `run_full_player.py`
   In `run_full_player.py` you will need to change the `bucket` variable
   You will very likely need to change the base assumptions in each of the files and definitely change
   vdl_project_template/player_building/player_attribute_settings.xlsx

Each step builds upon the previous one, creating a clear pipeline for data processing and player model development.

## Initial Setup

### Clone Key Repositories

1. Create a directory on your computer called `vdl` and clone the following repositories into it:

   - **VDL-Tools**: Our central repository for shared tools
   - **Shared-Data**: Repository for data shared across projects (project-specific data will live in project-specific repositories)

### Development Environment Setup

1. **Create a Virtual Environment**
   - Create a virtual environment named `vdl-env`
   - Note: If you use PyCharm, you may have your own preferred method for this

2. **Install VDL-Tools**
   - Install `vdl-tools` in your virtual environment using:
   ```bash
   pip install -e <path_to_vdl-tools_root>
   ```
   - Example: If you cloned vdl-tools at `/home/vdl/vdl-tools`, the command would be:
   ```bash
   pip install -e /home/vdl/vdl-tools
   ```

3. **Install Git LFS**
   - Install Git LFS (Large File Storage)
   - Note: This should be done before cloning shared-data

4. **Configuration Setup**
   - Ensure you have a `config.ini` file in your `vdl/` directory
   - Contact Lara Reichmann or Mike Tulubaev for a copy of this file
   - Set an environment variable pointing to your config.ini
   - For bash users, add this to your `.bashrc`:
   ```bash
   export VDL_CONFIG_PATH=/path/to/your/config.ini
   ```

## Project Setup

After cloning this template:
1. Rename the directory `vdl_project_template` to your project name
2. Update this README with project-specific information
3. Follow the setup instructions above to configure your development environment


## Running the Pipeline

To run the complete pipeline, execute the scripts in the following order:

1. **Data Preprocessing**
   ```bash
   python data_preprocessing/process_data.py
   ```
   This will create a unified dataset from the raw data.

2. **Data Enrichment**
   ```bash
   python data_enrichment/enrich_data.py
   ```
   This will add additional features to the preprocessed data.

3. **Player Building**
   ```bash
   python player_building/run_full_player.py
   ```
   This will generate the final player output.

Note: Make sure each step completes successfully before moving to the next one, as each script depends on the output of the previous step.
