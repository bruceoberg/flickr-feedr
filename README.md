# flickr-feedr
A python script that feeds a flat flickr export directory (from zips) into Apple's
Photos app on the Mac. It also puts images in appropriate folders from flickr.

This project was mostly written by [Claude](https://claude.ai), under my direction,
in [this chat](https://claude.ai/share/a919e8b4-5159-4eee-ac7b-51b53984bca4).

The chat started out as a discussion of where/how I could consolidate my photo
collection. Apple Photos is the default winner because my main camera is my
iPhone. While discussing other options, I was surprised that there is no
commercial service for syncing photos across services while maintaining
meta data such as folder structure. Specifically, flickr only offers a
minimal export function, where everything is exported via dozens of ZIP
files.

At some point we figured out that I needed a script do copy my flickr
photos/structure to Apple Photos. Claude offered to write this script
and I jokingly said "yes, but use Sucker Punch Hungarian coding conventions".

To my great surprise, Claude was able to write the script and actually use
coding conventions that resembled those from Sucker Punch (my former
employer). I took the script and edited to match the conventions I prefer
to use. Claude was able to read my changes and summarize the rules that
I use. Again, I was really surprised at its ability to do this.

The script ran ok, but got tripped up using the wrong mechanisms for error
catching. Claude's code was expecting python exceptions, but in some cases
the relevant module was returning None or errors instead. With a few fixes,
the code worked as advertised.

Running the script over my entire (~5000 pic) flickr library proved difficult.
The script used a python module that communicated with the Apple Photos app
running on the same Mac as the script. A couple hundred photos in, Apple
Photos would hang or throw a random error and the whole process would get
stopped up. The script needed to be more robust to upstream errors.

So I asked Claude to refactor the script into two phases: prep and execute.
I also asked it to track how far it made it through the list of pics to
process it. This is not a trivial programming task, and I was stunned that
Claude pumped it out in a minute or two. Because I was keeping all the source
in git, I could audit Claude's code and it all did what I had asked for.
Really impressive.

In short, Claude saved me a lot of time writing a script for moving data
between two systems that didn't want to cooperate. For small scale projects
like this, Claude is a useful tool.
