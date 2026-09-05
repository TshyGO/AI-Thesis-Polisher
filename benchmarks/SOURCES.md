# Public-derived corpus attribution and limitations

All `fair*` records in `public_derived.jsonl` derive from:

Mark D. Wilkinson et al. (2016), **The FAIR Guiding Principles for scientific data
management and stewardship**, Scientific Data 3, 160018.
DOI: https://doi.org/10.1038/sdata.2016.18
Full text: https://pmc.ncbi.nlm.nih.gov/articles/PMC4792175/
Retrieved 2026-09-06 using
https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4792175/fullTextXML

The full-text permissions explicitly license the work under **CC BY 4.0**:
https://creativecommons.org/licenses/by/4.0/
Copyright notice in source: Copyright © 2016, Macmillan Publishers Limited.
These excerpts and adaptations remain attributed to that source; the repository's
MIT license does not replace the source text's CC BY 4.0 license.

`*-keep`: exact selected published sentences, labelled KEEP under a conservative
minimal-grammar-edit policy by the coding agent, not a human annotation panel.
Source locations: fair01–02 abstract; fair03–05 body paragraph 3; fair06 paragraph 4;
fair07 paragraph 5; fair08 paragraph 6; fair09–10 paragraph 8 (XML paragraph order).

`*-error`: adaptations of the corresponding KEEP sentence, with a single
deliberately introduced subject–verb agreement or modal-verb error. The accepted
reference is the published original. These errors did NOT occur in the paper.
This is a controlled restoration benchmark, not a naturally occurring error corpus.

Ten source sentences yield twenty cases; they are paired and correlated, so do
not describe them as twenty independent real-paper samples. One paper/domain is
not representative of Chinese theses or materials-science editing. Freeze the
corpus before running model comparisons; do not add model outputs to accepted
references merely to improve the score. No human acceptance rate is available.
