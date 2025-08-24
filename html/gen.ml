(* https://dune.readthedocs.io/en/stable/howto/rule-generation.html#using-dynamic-include *)

let generate_rules base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_out.yaml
   (run %%{bin:hbt} -t yaml ../%s.html))))

(rule
 (package hbt)
 (alias runtest)
 (action
  (diff ../%s.yaml %s_out.yaml)))
|}
    base
    base
    base
    base

let generate_html_export_rules base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_export_out.html
   (run %%{bin:hbt} -t html ../%s.html))))

(rule
 (package hbt)
 (alias runtest)
 (action
  (diff ../export/%s_export.html %s_export_out.html)))
|}
    base
    base
    base
    base

let () =
  let html_files =
    Sys.readdir ".." |> Array.to_list |> List.filter_map (Filename.chop_suffix_opt ~suffix:".html")
  in
  List.iter generate_rules html_files;
  List.iter generate_html_export_rules html_files
