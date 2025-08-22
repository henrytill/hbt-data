let generate_xml_rule base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_out.yaml
   (setenv TZ UTC (run %%{bin:hbt} --dump ../%s.xml)))))

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

let generate_json_rule base =
  Printf.printf
    {|
(rule
 (package hbt)
 (action
  (with-stdout-to
   %s_out.yaml
   (setenv TZ UTC (run %%{bin:hbt} --dump ../%s.json)))))

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

let () =
  let xml_files = [ "empty"; "xml_sample" ] in
  let json_files = [ "json_sample" ] in
  List.iter generate_xml_rule xml_files;
  List.iter generate_json_rule json_files
