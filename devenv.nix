{ pkgs, lib, config, inputs, ... }:

{
  languages.python =
  {
    enable = true;
    venv.enable = true;
    venv.requirements = ./requirements.txt;
  };

  # See full reference at https://devenv.sh/reference/options/
}
